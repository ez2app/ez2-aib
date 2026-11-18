#![allow(clippy::unwrap_used)] // test code: a panic is the failure report

//! B1dm-lint acceptance: the old tests of r30_lint.py and vocative_lint.py carried over as they
//! were (vocative_lint.py `--selftest`, its exclusion self-check, test-dm-r30-lint-reason R2, the
//! two-layer exception rule of probe-r30-allowlist-equals-format, the autofix consistency note).

use aib_dmq::lint::{r30, vocative};
use std::collections::BTreeSet;

/// vocative_lint.py `_selftest` cases: (label, text, refused).
fn cases() -> Vec<(&'static str, String, bool)> {
    let v = vocative::VOCATIVE_CHARS[0];
    vec![
        ("行首", format!("{v}好"), true),
        ("句號後", format!("收到。{v}看一下"), true),
        ("換行後", format!("第一行\n{v}再看"), true),
        ("括號後", format!("({v})"), true),
        // a known false positive, kept as refused to be honest about it
        ("頓號後接以該字開頭的詞", format!("甲、{v}們兩個"), true),
        ("inline code", format!("指令是 `grep -c {v}` 這樣"), false),
        ("fenced", format!("```\n{v}\n```"), false),
        ("逐字引文", format!("> {v}說的那句"), false),
        ("詞中", format!("其{v}我我"), false),
        ("未閉合 fence 之後", format!("```\n{v}\n沒有收尾"), false),
        ("完全沒有那個字", "全部改用 @五碼".to_owned(), false),
    ]
}

#[test]
fn the_vocative_selftest_cases_hold() {
    for (label, text, refused) in cases() {
        assert_eq!(vocative::grammar_blocks(&text), refused, "reason: {label}");
    }
}

/// Without the exclusion layer the four excluded cases must turn refused (it is really at work).
#[test]
fn without_the_exclusions_the_four_excluded_cases_are_refused() {
    let excluded = ["inline code", "fenced", "逐字引文", "未閉合 fence 之後"];
    for (label, text, _) in cases().into_iter().filter(|c| excluded.contains(&c.0)) {
        let t: Vec<char> = text.chars().collect();
        assert!(
            !vocative::hits_outside(&t, &[]).is_empty(),
            "reason: exclusion_not_at_work {label}"
        );
    }
}

/// Offsets count characters and come out ascending.
#[test]
fn the_offsets_are_characters_in_order() {
    let v = vocative::VOCATIVE_CHARS[0];
    assert_eq!(
        vocative::vocative_hits(&format!("{v}好。{v}來")),
        vec![0, 3]
    );
    assert!(vocative::UNCOVERED_SECOND_PERSON
        .iter()
        .all(|c| !vocative::grammar_blocks(&c.to_string())));
}

/// R2: circled numbers are refused, and every finding line fits dm-ctl's position grep
/// `^[[:space:]]+[0-9]+:[0-9]+ ` with its code point.
#[test]
fn r30_findings_fit_the_position_grep() {
    let f = r30::lint("好 ①② 好");
    assert_eq!(f.len(), 2, "reason: both_listed");
    assert_eq!((f[0].line, f[0].col, f[0].code), (1, 3, "circled_number"));
    assert_eq!(f[0].suggestion.as_deref(), Some("1."));
    let out = r30::format_findings(&f);
    let lines: Vec<&str> = out.lines().collect();
    assert_eq!(lines.len(), 2);
    for l in &lines {
        let rest = l.trim_start();
        assert!(l.len() > rest.len(), "reason: no_leading_space {l}");
        let (pos, _) = rest.split_once(' ').unwrap();
        let (a, b) = pos.split_once(':').unwrap();
        assert!(
            a.bytes().all(|x| x.is_ascii_digit()) && b.bytes().all(|x| x.is_ascii_digit()),
            "reason: grep_shape {l}"
        );
    }
    assert!(
        out.contains("U+2460") && out.contains("  -> 建議:1."),
        "{out}"
    );
    // full-width punctuation is Chinese, never refused
    assert!(r30::lint("好,。「」:;?!").is_empty());
}

/// Each range and the suggestions (full-width -> ASCII, circled numbers -> n. / (n)).
#[test]
fn r30_ranges_and_suggestions() {
    for (text, code, sug) in [
        ("Ａ", "fullwidth_alnum", Some("A")),
        ("ｚ", "fullwidth_alnum", Some("z")),
        ("９", "fullwidth_alnum", Some("9")),
        ("⑳", "circled_number", Some("20.")),
        ("⑴", "circled_number", Some("(1)")),
        ("⒈", "circled_number", Some("1.")),
        ("Ⓐ", "circled_number", Some("A")),
        ("ⓩ", "circled_number", Some("z")),
        ("─", "box_drawing", None),
        ("█", "block_element", None),
        ("🄀", "circled_number", None),
    ] {
        let f = r30::lint(text);
        assert_eq!(f.len(), 1, "reason: not_refused {text}");
        assert_eq!(
            (f[0].code, f[0].suggestion.as_deref()),
            (code, sug),
            "{text}"
        );
    }
    let f = r30::lint("a\nbc①");
    assert_eq!((f[0].line, f[0].col), (2, 3), "reason: position");
}

/// Layer one: every exception is a light single tree connector. Layer two: the exceptions are
/// exactly the code points the ranges would refuse in the canonical sample.
#[test]
fn the_exception_is_what_the_canonical_format_uses() {
    let sample = include_str!("fixtures/r30-format-sample.txt");
    let allow: BTreeSet<u32> = r30::ALLOW_CODEPOINTS.iter().map(|(c, _)| *c).collect();
    assert!(allow.iter().all(|c| r30::LIGHT_TREE_CONNECTORS.contains(c)));
    let used: BTreeSet<u32> = sample
        .chars()
        .map(u32::from)
        .filter(|cp| r30::category(*cp).is_some())
        .collect();
    assert_eq!(allow, used, "reason: exception_not_the_format");
    assert!(
        r30::lint(sample).is_empty(),
        "reason: canonical_format_refused"
    );
    assert_eq!(
        r30::lint("─").len(),
        1,
        "reason: horizontal_line_let_through"
    );
}

/// autofix replaces what has a replacement, keeps the rest, never touches an allowed character.
#[test]
fn autofix_keeps_what_lint_lets_through() {
    assert_eq!(r30::autofix("①Ａ"), ("1.A".to_owned(), 0));
    assert_eq!(r30::autofix("─x"), ("─x".to_owned(), 1));
    let tree = "╭▸ a\n│\n├▸ b\n╰▸ c";
    assert_eq!(r30::autofix(tree), (tree.to_owned(), 0));
}

/// 500 mixed strings whose answers the Python r30_lint.py / vocative_lint.py gave
/// (fixtures/lint-py-vectors.json, made from aib/src/messaging at this commit): the same findings,
/// report, autofix and offsets, one by one.
#[test]
fn the_same_answers_as_the_python_checks() {
    let rows: Vec<serde_json::Value> =
        serde_json::from_str(include_str!("fixtures/lint-py-vectors.json")).unwrap();
    assert_eq!(rows.len(), 500);
    for r in &rows {
        let text = r["text"].as_str().unwrap();
        let f = r30::lint(text);
        let got: Vec<serde_json::Value> = f
            .iter()
            .map(|x| serde_json::json!([x.code, x.codepoint, x.line, x.col, x.suggestion]))
            .collect();
        assert_eq!(
            serde_json::Value::from(got),
            r["r30"],
            "reason: r30 {text:?}"
        );
        assert_eq!(
            r30::format_findings(&f),
            r["report"].as_str().unwrap(),
            "{text:?}"
        );
        let (fixed, left) = r30::autofix(text);
        assert_eq!(
            serde_json::json!([fixed, left]),
            r["fix"],
            "reason: autofix {text:?}"
        );
        let voc: Vec<u64> = r["voc"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_u64().unwrap())
            .collect();
        let got: Vec<u64> = vocative::vocative_hits(text)
            .into_iter()
            .map(|o| o as u64)
            .collect();
        assert_eq!(got, voc, "reason: vocative {text:?}");
    }
}

/// 5d33a REVIEW-B1dm-lint L1: each of the twenty characters vocative_lint.py's PREFIX lists
/// (written out here, not read from the const) opens a calling position; a letter does not.
#[test]
fn every_prefix_character_opens_a_calling_position() {
    let listed = [
        '\n', ' ', '\u{3000}', '，', '。', ':', ';', '：', '；', '？', '！', '、', '（', '）', ',',
        '.', '?', '!', '(', ')',
    ];
    assert_eq!(listed.len(), 20);
    for c in listed {
        assert_eq!(
            vocative::vocative_hits(&format!("x{c}\u{4F60}")),
            vec![2],
            "reason: prefix_char_lost {c:?}"
        );
    }
    assert!(vocative::vocative_hits("xa\u{4F60}").is_empty());
}
