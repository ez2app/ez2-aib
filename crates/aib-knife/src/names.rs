//! Test names in runner output, and the rule that decides whether a failed name is the
//! one a knife expects.
//!
//! Formats:
//! - cargo / libtest: `test a::b ... FAILED` (also `test a::b - should panic ... FAILED`),
//!   and the closing `failures:` list (the only names `cargo test -q` prints): every line
//!   that starts with four spaces is one name, whole line (doctest names have spaces:
//!   `src/lib.rs - foo (line 3)`), until an empty or unindented line;
//! - unittest: `FAIL: short (qual)` / `ERROR: short (qual)`. Always the **full** name
//!   `module.Class.short`: Python 3.11+ prints it as `qual`, 3.9 prints `module.Class`
//!   and the short name is appended (a9136 #586 M1);
//! - pytest: `FAILED <nodeid>`, the whole node id including the file (`tests/a.py::T::x`).
//!
//! knife.py kept only the short unittest name and dropped the pytest file, so two red
//! tests with the same short name looked like one (a false BIT; named difference in the
//! #586 old-vs-new table).
//!
//! Passed names (for `--require-pass-line`): libtest `test a::b ... ok` (with or without
//! ` - should panic`), unittest -v `short (qual) ... ok`, full name as above.

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

/// `test NAME[ - should panic] ... RESULT` (libtest) ⇒ NAME when RESULT matches.
fn libtest_line<'a>(line: &'a str, result: &str) -> Option<&'a str> {
    let rest = line.strip_prefix("test ")?;
    let name = token(rest)?;
    let mut tail = &rest[name.len()..];
    if let Some(t) = tail.strip_prefix(" - should panic") {
        tail = t;
    }
    if tail.starts_with(&format!(" ... {result}")) {
        Some(name)
    } else {
        None
    }
}

/// unittest's `short (qual)` ⇒ the full `module.Class.short` (3.11+ `qual` already ends
/// in `.short`; 3.9 `qual` is `module.Class`).
fn unittest_full(short: &str, qual: &str) -> Option<String> {
    let bad = |x: &str| x.is_empty() || x.contains(char::is_whitespace);
    if bad(short) || bad(qual) {
        return None;
    }
    if qual.ends_with(&format!(".{short}")) {
        Some(qual.to_string())
    } else {
        Some(format!("{qual}.{short}"))
    }
}

/// `short (qual)...` at the start of `s` ⇒ (short, qual).
fn short_and_qual(s: &str) -> Option<(&str, &str)> {
    let short = token(s)?;
    let after = s[short.len()..].strip_prefix(" (")?;
    let close = after.find(')')?;
    Some((short, &after[..close]))
}

/// Every failed test name in `out`, sorted, no duplicates.
pub fn failed_names(out: &str) -> Vec<String> {
    let mut got = BTreeSet::new();
    let mut in_failures = false;
    for line in lines(out) {
        if in_failures {
            if let Some(rest) = line.strip_prefix("    ") {
                let n = rest.trim_end();
                if !n.is_empty() {
                    got.insert(n.to_string());
                    continue;
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
            if let Some((short, qual)) = line.strip_prefix(p).and_then(short_and_qual) {
                if let Some(full) = unittest_full(short, qual) {
                    got.insert(full);
                }
            }
        }
        if let Some(t) = line.strip_prefix("FAILED ").and_then(token) {
            if t.contains("::") {
                got.insert(t.to_string());
            }
        }
    }
    got.into_iter().collect()
}

/// Every passed test name in `out` (libtest `... ok`, unittest -v `short (qual) ... ok`).
pub fn passed_names(out: &str) -> Vec<String> {
    let mut got = BTreeSet::new();
    for line in lines(out) {
        if let Some(n) = libtest_line(line, "ok") {
            got.insert(n.to_string());
            continue;
        }
        if let Some((short, qual)) = short_and_qual(line) {
            let shown = format!("{short} ({qual}) ... ok");
            if line.starts_with(&shown) {
                if let Some(full) = unittest_full(short, qual) {
                    got.insert(full);
                }
            }
        }
    }
    got.into_iter().collect()
}

/// `name` is the expected test: the whole name, or its last `::` / `.` segment.
/// `test_add` does not match `test_add_many` (1ced7 #525 H2).
pub fn name_matches(name: &str, expect: &str) -> bool {
    name == expect
        || name.ends_with(&format!("::{expect}"))
        || name.ends_with(&format!(".{expect}"))
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
    let m: Vec<String> = failed
        .iter()
        .filter(|n| name_matches(n, expect))
        .cloned()
        .collect();
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

    const PYTEST: &str =
        "FAILED tests/test_a.py::TestA::test_x - AssertionError\nFAILED nocolon - x\n";

    #[test]
    fn cargo_failed_names_from_lines_and_the_closing_list() {
        assert_eq!(
            failed_names(CARGO),
            vec!["job::tests::the_owner_crashing_kills_the_tree".to_string()]
        );
    }

    #[test]
    fn cargo_q_names_come_from_the_closing_list_only_crlf_too() {
        assert_eq!(
            failed_names(CARGO_Q),
            vec!["m::x".to_string(), "m::y".to_string()]
        );
    }

    #[test]
    fn unittest_fail_and_error_full_names() {
        assert_eq!(
            failed_names(UNITTEST),
            vec![
                "__main__.T.test_boom".to_string(),
                "__main__.T.test_sub".to_string()
            ]
        );
    }

    #[test]
    fn unittest_39_short_qualifier_gets_the_method_appended() {
        let out = "FAIL: test_sub (__main__.T)\nERROR: test_boom (__main__.T)\n";
        assert_eq!(
            failed_names(out),
            vec![
                "__main__.T.test_boom".to_string(),
                "__main__.T.test_sub".to_string()
            ]
        );
    }

    #[test]
    fn unittest_subtest_line_takes_the_first_parentheses() {
        let out = "FAIL: test_knives (__main__.Knife.test_knives) (tag='K1')\n";
        assert_eq!(
            failed_names(out),
            vec!["__main__.Knife.test_knives".to_string()]
        );
    }

    #[test]
    fn m1_two_unittest_classes_with_one_short_name_are_ambiguous() {
        let failed = failed_names("FAIL: test_sub (m.A.test_sub)\nFAIL: test_sub (m.B.test_sub)\n");
        assert_eq!(failed.len(), 2);
        assert!(matches!(
            find_expected(&failed, "test_sub"),
            Hit::Ambiguous(_)
        ));
        assert_eq!(
            find_expected(&failed, "A.test_sub"),
            Hit::One("m.A.test_sub".to_string())
        );
    }

    #[test]
    fn m1_qualified_expect_matches_unittest() {
        let failed = failed_names(UNITTEST);
        assert_eq!(
            find_expected(&failed, "T.test_sub"),
            Hit::One("__main__.T.test_sub".to_string())
        );
    }

    #[test]
    fn pytest_keeps_the_whole_node_id() {
        assert_eq!(
            failed_names(PYTEST),
            vec!["tests/test_a.py::TestA::test_x".to_string()]
        );
    }

    #[test]
    fn m1_two_pytest_files_with_one_tail_are_ambiguous() {
        let failed =
            failed_names("FAILED a.py::TestA::test_x - e\nFAILED b.py::TestA::test_x - e\n");
        assert!(matches!(
            find_expected(&failed, "TestA::test_x"),
            Hit::Ambiguous(_)
        ));
        assert!(matches!(
            find_expected(&failed, "test_x"),
            Hit::Ambiguous(_)
        ));
        assert_eq!(
            find_expected(&failed, "a.py::TestA::test_x"),
            Hit::One("a.py::TestA::test_x".to_string())
        );
    }

    #[test]
    fn l1_should_panic_lines() {
        assert_eq!(
            passed_names("test m::x - should panic ... ok\n"),
            vec!["m::x".to_string()]
        );
        assert_eq!(
            failed_names("test m::y - should panic ... FAILED\n"),
            vec!["m::y".to_string()]
        );
    }

    #[test]
    fn l2_doctest_names_do_not_end_the_closing_list() {
        let out = "failures:\n    src/lib.rs - foo (line 3)\n    m::x\n\ntest result: FAILED.\n";
        assert_eq!(
            failed_names(out),
            vec!["m::x".to_string(), "src/lib.rs - foo (line 3)".to_string()]
        );
    }

    #[test]
    fn the_stdout_header_is_not_a_name() {
        assert!(!failed_names(CARGO).iter().any(|n| n.starts_with("----")));
    }

    #[test]
    fn passed_names_libtest_and_unittest_verbose_full_names() {
        let p = passed_names(CARGO);
        assert_eq!(p, vec!["job::tests::a".to_string()]);
        assert_eq!(
            passed_names(UNITTEST),
            vec!["__main__.T.test_add".to_string()]
        );
        assert_eq!(
            passed_names("test_add (__main__.T) ... ok\n"),
            vec!["__main__.T.test_add".to_string()]
        );
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
        assert_eq!(
            find_expected(&failed, "test_add"),
            Hit::Ambiguous(failed.clone())
        );
        assert_eq!(
            find_expected(&failed, "a::test_add"),
            Hit::One("a::test_add".to_string())
        );
    }

    #[test]
    fn m3_one_full_path_is_one() {
        let failed = ["a::test_add".to_string()];
        assert_eq!(
            find_expected(&failed, "test_add"),
            Hit::One("a::test_add".to_string())
        );
    }

    #[test]
    fn build_failure_markers() {
        assert!(build_failed("error: could not compile `x` (lib test)"));
        assert!(build_failed("error[E0425]: cannot find value"));
        assert!(build_failed("x\nSyntaxError: invalid syntax"));
        assert!(build_failed(
            "  File x\nIndentationError: unexpected indent"
        ));
        assert!(build_failed("foo.obj : error LNK2019: unresolved"));
        assert!(build_failed("error: linking with `cc` failed"));
        assert!(!build_failed("error[E]: no digits"));
        assert!(!build_failed("LNK20 short"));
        assert!(!build_failed("test x ... FAILED"));
        assert!(!build_failed("  SyntaxError: indented is not a line start"));
    }
}
