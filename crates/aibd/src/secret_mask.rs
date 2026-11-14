//! Known secret formats masked out of a session's output before it is stored (T80):
//! a key a program printed (a Claude tool result that read a `.env`, a `printenv`)
//! must not sit in the event store and come back through the output API.
//!
//! Only formats with a fixed prefix are masked (`sk-ant-…`, `ghp_…`, `AKIA…`,
//! `Bearer …`, JWTs, …). A "long random-looking word" rule is not used: Claude Code's
//! stream-json lines are full of long ids (`msg_…`, `toolu_…`, uuids, hashes) that must
//! stay readable. Best effort: a secret in a format not listed here is stored as
//! printed (the store stays owner-only, `session_output`).
//!
//! The prefix stays and the rest becomes `***`, so a reader still sees what kind of
//! key was there. Only ASCII is touched, so a JSON line stays valid JSON.
//!
//! A key must start a word. In a stored line a word also starts right after a JSON
//! escape (`\n`, `\t`, `\r`: a key alone on a line of a tool result) and after an
//! ANSI colour code (`ESC[…m`, raw or written `\u001b[…m` in JSON) — 043dd T80 M1.

use std::borrow::Cow;

/// One known format: `prefix` then a body of token characters.
struct Rule {
    prefix: &'static str,
    /// Body length range (token characters after the prefix).
    min: usize,
    max: usize,
    /// Characters allowed in the body besides `A-Z a-z 0-9 _ -`.
    extra: &'static str,
    /// The body must hold a digit (keeps words like `sk-learn-style` readable).
    digit: bool,
    /// The body must hold this many dots (a JWT is three dot-separated parts).
    dots: usize,
    /// The body is upper case and digits only (AWS key ids).
    upper: bool,
}

const fn rule(prefix: &'static str, min: usize, max: usize) -> Rule {
    Rule {
        prefix,
        min,
        max,
        extra: "",
        digit: false,
        dots: 0,
        upper: false,
    }
}

/// Longer prefixes first: the first rule that matches wins.
const RULES: &[Rule] = &[
    rule("sk-ant-", 16, usize::MAX),
    rule("sk-proj-", 16, usize::MAX),
    Rule {
        digit: true,
        ..rule("sk-", 20, usize::MAX)
    },
    rule("github_pat_", 30, usize::MAX),
    rule("ghp_", 30, usize::MAX),
    rule("gho_", 30, usize::MAX),
    rule("ghs_", 30, usize::MAX),
    rule("ghu_", 30, usize::MAX),
    rule("ghr_", 30, usize::MAX),
    rule("glpat-", 20, usize::MAX),
    rule("xoxb-", 10, usize::MAX),
    rule("xoxp-", 10, usize::MAX),
    rule("xoxa-", 10, usize::MAX),
    rule("xoxr-", 10, usize::MAX),
    rule("xoxs-", 10, usize::MAX),
    Rule {
        upper: true,
        ..rule("AKIA", 16, 16)
    },
    Rule {
        upper: true,
        ..rule("ASIA", 16, 16)
    },
    rule("AIza", 35, 35),
    Rule {
        extra: ".",
        dots: 2,
        ..rule("eyJ", 30, usize::MAX)
    },
    Rule {
        extra: "._~+/=",
        ..rule("Bearer ", 16, usize::MAX)
    },
    Rule {
        extra: "._~+/=",
        ..rule("bearer ", 16, usize::MAX)
    },
];

fn is_tok(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_' || b == b'-'
}

/// A word can start at `i`: the start of the line, after a non-token byte, after a
/// JSON escape `\n` `\t` `\r`, or after an ANSI colour code.
fn word_start(bytes: &[u8], i: usize) -> bool {
    let before = &bytes[..i];
    let Some(&last) = before.last() else {
        return true;
    };
    if !is_tok(last) {
        return true;
    }
    if matches!(last, b'n' | b't' | b'r') && before[..i - 1].ends_with(b"\\") {
        return true;
    }
    // `ESC [ <digits and ;> m`, the escape raw or as JSON writes it
    if let Some(rest) = before.strip_suffix(b"m") {
        let params = rest
            .iter()
            .rev()
            .take_while(|c| c.is_ascii_digit() || **c == b';')
            .count();
        if let Some(rest) = rest[..rest.len() - params].strip_suffix(b"[") {
            return rest.ends_with(b"\x1b")
                || rest.ends_with(b"\\u001b")
                || rest.ends_with(b"\\u001B");
        }
    }
    false
}

/// The body length of `r` at `rest` (the text after the prefix), if it is a match.
fn body(r: &Rule, rest: &[u8]) -> Option<usize> {
    let n = rest
        .iter()
        .take_while(|&&b| is_tok(b) || r.extra.as_bytes().contains(&b))
        .count();
    let b = &rest[..n];
    // `take_while` stopped at a non-token byte, so a longer word never fits `max`
    let ok = (r.min..=r.max).contains(&n)
        && (!r.digit || b.iter().any(u8::is_ascii_digit))
        && (!r.upper
            || b.iter()
                .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit()))
        && b.iter().filter(|&&c| c == b'.').count() >= r.dots;
    ok.then_some(n)
}

/// `line` with every known secret format masked (borrowed when there is none).
pub fn mask(line: &str) -> Cow<'_, str> {
    let bytes = line.as_bytes();
    let mut out: Option<String> = None;
    let mut done = 0; // bytes of `line` already in `out`
    let mut i = 0;
    while i < bytes.len() {
        // a key starts a word: `risk-…` never matches `sk-`
        if word_start(bytes, i) {
            let hit = RULES.iter().find_map(|r| {
                let p = r.prefix.as_bytes();
                if bytes[i..].starts_with(p) {
                    body(r, &bytes[i + p.len()..]).map(|n| (r, p.len() + n))
                } else {
                    None
                }
            });
            if let Some((r, len)) = hit {
                let o = out.get_or_insert_with(|| String::with_capacity(line.len()));
                // `i` and `i + len` sit on ASCII bytes: char boundaries
                o.push_str(&line[done..i]);
                o.push_str(r.prefix);
                o.push_str("***");
                i += len;
                done = i;
                continue;
            }
        }
        i += 1;
    }
    match out {
        None => Cow::Borrowed(line),
        Some(mut o) => {
            o.push_str(&line[done..]);
            Cow::Owned(o)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Key-shaped test strings are put together at run time, so no whole key-shaped
    /// string sits in the source for a secret scanner to stop.
    fn k(prefix: &str, body: &str) -> String {
        format!("{prefix}{body}")
    }

    #[test]
    fn known_formats_are_masked_and_keep_their_prefix() {
        let cases = [
            (
                format!(
                    "ANTHROPIC_API_KEY={}",
                    k("sk-ant-", "api03-AbCdEf0123456789xyz")
                ),
                "ANTHROPIC_API_KEY=sk-ant-***",
            ),
            (
                format!(
                    "token {} end",
                    k("ghp_", "0123456789abcdefghijABCDEFGHIJklmnop")
                ),
                "token ghp_*** end",
            ),
            (
                format!("aws {} x", k("AKIA", "IOSFODNN7EXAMPLE")),
                "aws AKIA*** x",
            ),
            (
                format!("Authorization: {}", k("Bearer ", "abc.DEF-123_456~789+/=")),
                "Authorization: Bearer ***",
            ),
            (
                format!(
                    "jwt {}",
                    k(
                        "eyJ",
                        "hbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJlXzEyMw"
                    )
                ),
                "jwt eyJ***",
            ),
            (
                format!("k={}", k("sk-", "0123456789abcdefghijKLMN")),
                "k=sk-***",
            ),
            (k("xoxb-", "12345-67890-abcdef"), "xoxb-***"),
        ];
        for (inp, want) in cases {
            assert_eq!(mask(&inp), want, "{inp}");
        }
    }

    #[test]
    fn a_json_line_stays_json_and_ids_stay_readable() {
        let line = serde_json::json!({
            "type": "assistant",
            "id": "msg_01AbCdEfGhIjKlMnOpQrStUv",
            "session_id": "0b6c8e4e-2f7c-4c3e-9d3a-1c2b3a4d5e6f",
            "text": format!("key {}", k("sk-ant-", "api03-XYZ0123456789abcdef")),
        })
        .to_string();
        let m = mask(&line);
        let v: serde_json::Value = serde_json::from_str(&m).unwrap();
        assert_eq!(v["text"], "key sk-ant-***");
        assert_eq!(v["id"], "msg_01AbCdEfGhIjKlMnOpQrStUv");
        assert_eq!(v["session_id"], "0b6c8e4e-2f7c-4c3e-9d3a-1c2b3a4d5e6f");
    }

    #[test]
    fn words_that_only_look_alike_are_left_alone() {
        for s in [
            "risk-assessment-0123456789abcdefghij".to_string(), // `sk-` inside a word
            "sk-learn-style-without-any-digits-at-all".to_string(),
            k("ghp_", "short"),
            k("AKIA", "IOSFODNN7EXAMPLEX"), // 17 after AKIA: not a key id
            k("AKIA", "iosfodnn7example"),  // lower case
            "eyJ.not.a.jwt".to_string(),
            "Bearer short".to_string(),
            "a 40-hex commit 0123456789abcdef0123456789abcdef01234567".to_string(),
            "naïve text — with non-ASCII ✓".to_string(),
            r"\nrisk-assessment-0123456789abcdefghij".to_string(), // escape, then no key
            format!("warm{}", k("ghp_", "0123456789abcdefghijABCDEFGHIJ")), // `m`, no ESC[
        ] {
            assert!(matches!(mask(&s), Cow::Borrowed(_)), "{s} -> {}", mask(&s));
        }
    }

    /// 043dd T80 M1: Claude's stream-json writes a tool result's line break as `\n`, so a
    /// key alone on a line follows an `n`; coloured output puts it after `…m`.
    #[test]
    fn a_key_after_a_json_escape_or_a_colour_code_is_masked() {
        let ghp = k("ghp_", "0123456789abcdefghijABCDEFGHIJ");
        let cases = [
            (
                format!(r#"{{"content":"line1\n{ghp}"}}"#),
                r#"{"content":"line1\nghp_***"}"#,
            ),
            (
                format!(r"a\t{}", k("AKIA", "IOSFODNN7EXAMPLE")),
                r"a\tAKIA***",
            ),
            (format!(r"x\r{ghp}"), r"x\rghp_***"),
            (
                format!(r"\u001b[32m{}", k("sk-ant-", "api03-AbCdEf0123456789xyz")),
                r"\u001b[32msk-ant-***",
            ),
            (
                format!("\x1b[1;32m{ghp}\x1b[0m"),
                "\x1b[1;32mghp_***\x1b[0m",
            ),
            (format!("\x1b[m{ghp}"), "\x1b[mghp_***"),
        ];
        for (inp, want) in cases {
            assert_eq!(mask(&inp), want, "{inp}");
        }
        // the JSON line stays JSON and the key is gone from the value
        let line = serde_json::json!({"content": format!("a\n{ghp}\nb")}).to_string();
        let v: serde_json::Value = serde_json::from_str(&mask(&line)).unwrap();
        assert_eq!(v["content"], "a\nghp_***\nb");
    }

    #[test]
    fn every_key_on_a_line_is_masked_and_non_ascii_around_it_survives() {
        let s = format!(
            "前 {} 中 {} 後",
            k("ghp_", "0123456789abcdefghijABCDEFGHIJ"),
            k("AKIA", "IOSFODNN7EXAMPLE")
        );
        assert_eq!(mask(&s), "前 ghp_*** 中 AKIA*** 後");
    }
}
