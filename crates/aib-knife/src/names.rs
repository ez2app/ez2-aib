//! Test names in runner output, and the rule that decides whether a failed name is the
//! one a knife expects.
//!
//! Formats (the same ones knife.py reads):
//! - cargo / libtest: `test a::b ... FAILED`, and the closing `failures:` list (the only
//!   names `cargo test -q` prints), whose lines are four spaces + the name;
//! - unittest: `FAIL: name (` / `ERROR: name (`;
//! - pytest: `FAILED path::name` (everything after the first `::`).
//!
//! Passed names (for `--require-pass-line`): libtest `test a::b ... ok`, unittest -v
//! `name (qualified.name) ... ok` (both the short and the qualified name count).

use std::collections::BTreeSet;

fn lines(out: &str) -> impl Iterator<Item = &str> {
    out.split('\n').map(|l| l.strip_suffix('\r').unwrap_or(l))
}

fn token(s: &str) -> Option<&str> {
    let t = s.split(char::is_whitespace).next().unwrap_or("");
    if t.is_empty() {
        None
    } else {
        Some(t)
    }
}

/// `test NAME ... RESULT` (libtest) ⇒ NAME when RESULT matches.
fn libtest_line<'a>(line: &'a str, result: &str) -> Option<&'a str> {
    let rest = line.strip_prefix("test ")?;
    let name = token(rest)?;
    let tail = &rest[name.len()..];
    let want = format!(" ... {result}");
    if tail.starts_with(&want) {
        Some(name)
    } else {
        None
    }
}

/// Every failed test name in `out`, sorted, no duplicates.
pub fn failed_names(out: &str) -> Vec<String> {
    let mut got = BTreeSet::new();
    let mut in_failures = false;
    for line in lines(out) {
        if in_failures {
            if let Some(rest) = line.strip_prefix("    ") {
                if let Some(t) = token(rest) {
                    if t.len() == rest.len() {
                        got.insert(t.to_string());
                        continue;
                    }
                }
            }
            in_failures = false;
        }
        if line == "failures:" {
            in_failures = true;
            continue;
        }
        if let Some(n) = libtest_line(line, "FAILED") {
            got.insert(n.to_string());
            continue;
        }
        for p in ["FAIL: ", "ERROR: "] {
            if let Some(rest) = line.strip_prefix(p) {
                if let Some(n) = token(rest) {
                    if rest[n.len()..].starts_with(" (") {
                        got.insert(n.to_string());
                    }
                }
            }
        }
        if let Some(rest) = line.strip_prefix("FAILED ") {
            if let Some(t) = token(rest) {
                if let Some(i) = t.find("::") {
                    let n = &t[i + 2..];
                    if !n.is_empty() {
                        got.insert(n.to_string());
                    }
                }
            }
        }
    }
    got.into_iter().collect()
}

/// Every passed test name in `out` (libtest `... ok`, unittest -v `name (qual) ... ok`).
pub fn passed_names(out: &str) -> Vec<String> {
    let mut got = BTreeSet::new();
    for line in lines(out) {
        if let Some(n) = libtest_line(line, "ok") {
            got.insert(n.to_string());
            continue;
        }
        // unittest -v: "test_add (__main__.T.test_add) ... ok"
        if let Some(open) = line.find(" (") {
            let short = &line[..open];
            let after = &line[open + 2..];
            if let Some(close) = after.find(") ... ok") {
                let qual = &after[..close];
                if !short.is_empty() && !short.contains(char::is_whitespace) {
                    got.insert(short.to_string());
                    if !qual.is_empty() && !qual.contains(char::is_whitespace) {
                        got.insert(qual.to_string());
                    }
                }
            }
        }
    }
    got.into_iter().collect()
}

/// `name` is the expected test: the whole name, or its last `::` / `.` segment.
/// `test_add` does not match `test_add_many` (1ced7 #525 H2).
pub fn name_matches(name: &str, expect: &str) -> bool {
    name == expect || name.ends_with(&format!("::{expect}")) || name.ends_with(&format!(".{expect}"))
}

/// What the failed names say about the expected test.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Hit {
    /// Exactly one failed name matches.
    One(String),
    /// A short expected name (no `::` / `.`) matches two or more different full names:
    /// we cannot tell which test went red (Codex M3).
    Ambiguous(Vec<String>),
    None,
}

pub fn find_expected(failed: &[String], expect: &str) -> Hit {
    let m: Vec<String> = failed.iter().filter(|n| name_matches(n, expect)).cloned().collect();
    match m.len() {
        0 => Hit::None,
        1 => Hit::One(m.into_iter().next().unwrap_or_default()),
        _ if expect.contains("::") || expect.contains('.') => {
            // a qualified name can still match itself and a longer path ending in it
            match m.iter().find(|n| n.as_str() == expect) {
                Some(n) => Hit::One(n.clone()),
                None => Hit::Ambiguous(m),
            }
        }
        _ => Hit::Ambiguous(m),
    }
}

/// The knifed code did not build (so nothing was tested).
pub fn build_failed(out: &str) -> bool {
    if out.contains("could not compile") || out.contains("error: linking") {
        return true;
    }
    if has_lnk_code(out) {
        return true;
    }
    lines(out).any(|l| {
        l.starts_with("SyntaxError:")
            || l.starts_with("IndentationError:")
            || l.strip_prefix("error[E").is_some_and(|r| {
                let digits = r.chars().take_while(char::is_ascii_digit).count();
                digits > 0 && r[digits..].starts_with(']')
            })
    })
}

fn has_lnk_code(out: &str) -> bool {
    let b = out.as_bytes();
    out.match_indices("LNK")
        .any(|(i, _)| b.len() >= i + 7 && b[i + 3..i + 7].iter().all(u8::is_ascii_digit))
}

#[cfg(test)]
mod tests {
    use super::*;

    const CARGO: &str = "running 3 tests\n\
test job::tests::a ... ok\n\
test job::tests::the_owner_crashing_kills_the_tree ... FAILED\n\
test job::tests::b ... ignored\n\
\n\
failures:\n\
\n\
---- job::tests::the_owner_crashing_kills_the_tree stdout ----\n\
thread panicked\n\
\n\
failures:\n    job::tests::the_owner_crashing_kills_the_tree\n\
\n\
test result: FAILED. 1 passed; 1 failed\n";

    // `cargo test -q` prints dots, then only the closing list
    const CARGO_Q: &str = "running 2 tests\r\n.F\r\nfailures:\r\n\r\n---- m::x stdout ----\r\n\r\nfailures:\r\n    m::x\r\n    m::y\r\n\r\ntest result: FAILED.\r\n";

    const UNITTEST: &str = "test_add (__main__.T.test_add) ... ok\n\
test_sub (__main__.T.test_sub) ... FAIL\n\
\n\
======================================================================\n\
FAIL: test_sub (__main__.T.test_sub)\n\
ERROR: test_boom (__main__.T.test_boom)\n";

    const PYTEST: &str = "FAILED tests/test_a.py::TestA::test_x - AssertionError\nFAILED nocolon - x\n";

    #[test]
    fn cargo_failed_names_from_lines_and_the_closing_list() {
        assert_eq!(failed_names(CARGO), vec!["job::tests::the_owner_crashing_kills_the_tree".to_string()]);
    }

    #[test]
    fn cargo_q_names_come_from_the_closing_list_only_crlf_too() {
        assert_eq!(failed_names(CARGO_Q), vec!["m::x".to_string(), "m::y".to_string()]);
    }

    #[test]
    fn unittest_fail_and_error() {
        assert_eq!(failed_names(UNITTEST), vec!["test_boom".to_string(), "test_sub".to_string()]);
    }

    #[test]
    fn pytest_takes_everything_after_the_first_double_colon() {
        assert_eq!(failed_names(PYTEST), vec!["TestA::test_x".to_string()]);
    }

    #[test]
    fn the_stdout_header_is_not_a_name() {
        assert!(!failed_names(CARGO).iter().any(|n| n.starts_with("----")));
    }

    #[test]
    fn passed_names_libtest_and_unittest_verbose() {
        let p = passed_names(CARGO);
        assert_eq!(p, vec!["job::tests::a".to_string()]);
        let u = passed_names(UNITTEST);
        assert!(u.contains(&"test_add".to_string()));
        assert!(u.contains(&"__main__.T.test_add".to_string()));
        assert!(!u.contains(&"test_sub".to_string()));
    }

    #[test]
    fn h2_a_prefix_is_not_a_match() {
        assert!(!name_matches("test_add_many", "test_add"));
        assert!(!name_matches("m::test_add_many", "test_add"));
        assert!(name_matches("m::test_add", "test_add"));
        assert!(name_matches("__main__.T.test_add", "test_add"));
        assert!(name_matches("test_add", "test_add"));
        assert!(!name_matches("xtest_add", "test_add"));
    }

    #[test]
    fn h2_regression_red_neighbour_does_not_count() {
        let failed = ["test_add_many".to_string(), "test_sub".to_string()];
        assert_eq!(find_expected(&failed, "test_add"), Hit::None);
    }

    #[test]
    fn m3_two_tests_with_the_same_tail_are_ambiguous() {
        let failed = vec!["a::test_add".to_string(), "b::test_add".to_string()];
        assert_eq!(find_expected(&failed, "test_add"), Hit::Ambiguous(failed.clone()));
        assert_eq!(find_expected(&failed, "a::test_add"), Hit::One("a::test_add".to_string()));
    }

    #[test]
    fn m3_one_full_path_is_one() {
        let failed = ["a::test_add".to_string()];
        assert_eq!(find_expected(&failed, "test_add"), Hit::One("a::test_add".to_string()));
    }

    #[test]
    fn build_failure_markers() {
        assert!(build_failed("error: could not compile `x` (lib test)"));
        assert!(build_failed("error[E0425]: cannot find value"));
        assert!(build_failed("x\nSyntaxError: invalid syntax"));
        assert!(build_failed("  File x\nIndentationError: unexpected indent"));
        assert!(build_failed("foo.obj : error LNK2019: unresolved"));
        assert!(build_failed("error: linking with `cc` failed"));
        assert!(!build_failed("error[E]: no digits"));
        assert!(!build_failed("LNK20 short"));
        assert!(!build_failed("test x ... FAILED"));
        assert!(!build_failed("  SyntaxError: indented is not a line start"));
    }
}
