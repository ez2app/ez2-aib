//! The two outgoing-message checks of 1.x, translated as they are (B1dm-lint; 519d0 11-18 07:34:
//! the same logic, the old tests carried over as the acceptance, nothing redesigned):
//!
//! - [`r30`]: `aib/src/messaging/r30_lint.py`: circled numbers, full-width letters and digits,
//!   box drawing and block elements are refused; four tree connectors of the canonical report
//!   format are let through; full-width punctuation is never refused (it is how Chinese is written).
//! - [`vocative`]: `aib/src/messaging/vocative_lint.py`: the second-person word in a calling
//!   position (start, or after a listed separator), outside code and quoted lines. Whether the
//!   message has more than one receiver is the caller's question, not this module's.
//!
//! Line and column numbers, and the offsets, count characters (Python `str` indexes), not bytes.

/// R-30: which characters a message may not carry, and the safe replacements.
pub mod r30 {
    /// One row of [`RANGES`]: `(code, label, ranges)`, ranges inclusive.
    pub type Range = (&'static str, &'static str, &'static [(u32, u32)]);

    /// The table of r30_lint.py `RANGES`.
    pub const RANGES: &[Range] = &[
        (
            "circled_number",
            "圈號/圈圈字",
            &[
                (0x2460, 0x24FF),
                (0x3248, 0x324F),
                (0x3251, 0x325F),
                (0x3280, 0x32BF),
                (0x1F100, 0x1F1FF),
            ],
        ),
        (
            "fullwidth_alnum",
            "全形英數",
            &[(0xFF10, 0xFF19), (0xFF21, 0xFF3A), (0xFF41, 0xFF5A)],
        ),
        ("box_drawing", "框線字元", &[(0x2500, 0x257F)]),
        ("block_element", "區塊填充字元", &[(0x2580, 0x259F)]),
    ];

    /// Layer one of the exception: light single tree connectors of Box Drawing.
    pub const LIGHT_TREE_CONNECTORS: &[u32] = &[
        0x2502, 0x250C, 0x2510, 0x2514, 0x2518, 0x251C, 0x2524, 0x252C, 0x2534, 0x253C, 0x2500,
        0x256D, 0x256E, 0x256F, 0x2570,
    ];

    /// The exception (code points, not the characters, and why): what the canonical report
    /// format (`r30-format-sample.txt`) uses. Layer two: this set equals the code points of the
    /// sample the ranges would refuse.
    pub const ALLOW_CODEPOINTS: &[(u32, &str)] = &[
        (
            0x256D,
            "樹狀回報的開頭圓角;正典格式 r30-format-sample.txt 第一項用它",
        ),
        (0x251C, "樹狀回報的中間分叉;同上,表示後面還有同層項目"),
        (0x2570, "樹狀回報的結尾轉角;同上,表示本層最後一項"),
        (0x2502, "樹狀回報的垂直連線;同上,表示上層尚未結束"),
    ];

    /// One refused character.
    #[derive(Debug, Clone, PartialEq, Eq)]
    pub struct Finding {
        pub code: &'static str,
        pub label: &'static str,
        pub ch: char,
        /// `U+XXXX` (at least four upper-case hex digits).
        pub codepoint: String,
        /// From 1.
        pub line: usize,
        /// From 1, in characters.
        pub col: usize,
        pub suggestion: Option<String>,
    }

    pub fn allowed(cp: u32) -> bool {
        ALLOW_CODEPOINTS.iter().any(|(c, _)| *c == cp)
    }

    /// The range a code point falls in: `(code, label)`.
    pub fn category(cp: u32) -> Option<(&'static str, &'static str)> {
        RANGES.iter().find_map(|(code, label, ranges)| {
            ranges
                .iter()
                .any(|(lo, hi)| (*lo..=*hi).contains(&cp))
                .then_some((*code, *label))
        })
    }

    fn shift(ch: char, from: u32, to: u32) -> Option<String> {
        char::from_u32(u32::from(ch) - from + to).map(String::from)
    }

    /// A safe replacement, or `None` (a person decides; the content is not guessed).
    pub fn suggest(ch: char) -> Option<String> {
        let cp = u32::from(ch);
        match cp {
            // full-width letters and digits (NFKC): ＡＢ１ -> AB1
            0xFF10..=0xFF19 | 0xFF21..=0xFF3A | 0xFF41..=0xFF5A => shift(ch, 0xFEE0, 0),
            0x2460..=0x2473 => Some(format!("{}.", cp - 0x2460 + 1)),
            0x2474..=0x2487 => Some(format!("({})", cp - 0x2474 + 1)),
            0x2488..=0x249B => Some(format!("{}.", cp - 0x2488 + 1)),
            // circled letters (NFKC): Ⓐ-Ⓩ -> A-Z, ⓐ-ⓩ -> a-z
            0x24B6..=0x24CF => shift(ch, 0x24B6, u32::from('A')),
            0x24D0..=0x24E9 => shift(ch, 0x24D0, u32::from('a')),
            _ => None,
        }
    }

    /// Every refused character of `text`.
    pub fn lint(text: &str) -> Vec<Finding> {
        let mut out = Vec::new();
        for (l, line) in text.split('\n').enumerate() {
            for (c, ch) in line.chars().enumerate() {
                let cp = u32::from(ch);
                if allowed(cp) {
                    continue;
                }
                if let Some((code, label)) = category(cp) {
                    out.push(Finding {
                        code,
                        label,
                        ch,
                        codepoint: format!("U+{cp:04X}"),
                        line: l + 1,
                        col: c + 1,
                        suggestion: suggest(ch),
                    });
                }
            }
        }
        out
    }

    /// Replace what has a safe replacement; leave the rest (the author decides). Returns the
    /// text and how many refused characters remain. An allowed character is never replaced.
    pub fn autofix(text: &str) -> (String, usize) {
        let mut fixed = String::with_capacity(text.len());
        for ch in text.chars() {
            let cp = u32::from(ch);
            match (allowed(cp), category(cp), suggest(ch)) {
                (false, Some(_), Some(s)) => fixed.push_str(&s),
                _ => fixed.push(ch),
            }
        }
        let left = lint(&fixed).len();
        (fixed, left)
    }

    /// The report, one line per finding: `  <line>:<col> <char> <label>(U+XXXX)` and
    /// `  -> 建議:<x>` when there is one (the shape dm-ctl's position grep reads).
    pub fn format_findings(findings: &[Finding]) -> String {
        findings
            .iter()
            .map(|f| {
                let sug = f
                    .suggestion
                    .as_ref()
                    .map(|s| format!("  -> 建議:{s}"))
                    .unwrap_or_default();
                format!(
                    "  {}:{} {} {}({}){}",
                    f.line, f.col, f.ch, f.label, f.codepoint, sug
                )
            })
            .collect::<Vec<_>>()
            .join("\n")
    }
}

/// EH.20 §5.3: the second-person word in a calling position.
pub mod vocative {
    /// The word, written by code point (talking about it must not trip the check).
    pub const VOCATIVE_CHARS: &[char] = &['\u{4F60}'];

    /// Known gap, not enabled: no fixture covers them, and enabling them would refuse drafts
    /// that pass today.
    pub const UNCOVERED_SECOND_PERSON: &[char] = &['\u{59B3}', '\u{60A8}'];

    /// What may stand right before a calling position (offset 0 is one too).
    pub const PREFIX: &str = "\n \u{3000}，。:;：；？！、（）,.?!()";

    fn find_at(t: &[char], at: usize, pat: &[char]) -> bool {
        t.get(at..at + pat.len()) == Some(pat)
    }

    /// The spans left out (character offsets, end exclusive): fenced blocks (an unclosed one
    /// runs to the end), inline code outside them, and whole lines starting with `>`.
    pub fn excluded_spans(t: &[char]) -> Vec<(usize, usize)> {
        let fence = ['`', '`', '`'];
        let mut idx = Vec::new();
        let mut i = 0;
        while i < t.len() {
            if find_at(t, i, &fence) {
                idx.push(i);
                i += 3;
            } else {
                i += 1;
            }
        }
        let mut spans: Vec<(usize, usize)> = idx
            .chunks(2)
            .map(|p| (p[0], p.get(1).map_or(t.len(), |e| e + 3)))
            .collect();
        // `[^`\n]*` scanned left to right, never overlapping
        let mut i = 0;
        while i < t.len() {
            if t[i] == '`' {
                let mut j = i + 1;
                while j < t.len() && t[j] != '`' && t[j] != '\n' {
                    j += 1;
                }
                if j < t.len() && t[j] == '`' {
                    if !spans.iter().any(|(s, e)| *s <= i && i < *e) {
                        spans.push((i, j + 1));
                    }
                    i = j + 1;
                    continue;
                }
            }
            i += 1;
        }
        // (?m)^>.*$
        let mut start = 0;
        for (n, ch) in t.iter().chain(std::iter::once(&'\n')).enumerate() {
            if *ch == '\n' {
                if t.get(start) == Some(&'>') {
                    spans.push((start, n));
                }
                start = n + 1;
            }
        }
        spans
    }

    /// The calling-position offsets (characters, ascending) given the left-out spans.
    pub fn hits_outside(t: &[char], spans: &[(usize, usize)]) -> Vec<usize> {
        t.iter()
            .enumerate()
            .filter(|(off, ch)| {
                VOCATIVE_CHARS.contains(ch)
                    && !spans.iter().any(|(s, e)| s <= off && off < e)
                    && (*off == 0 || PREFIX.contains(t[off - 1]))
            })
            .map(|(off, _)| off)
            .collect()
    }

    /// Every calling-position offset of `txt` (characters, ascending).
    pub fn vocative_hits(txt: &str) -> Vec<usize> {
        let t: Vec<char> = txt.chars().collect();
        hits_outside(&t, &excluded_spans(&t))
    }

    /// Whether `txt` has the word in a calling position (check_labels.py's name).
    pub fn grammar_blocks(txt: &str) -> bool {
        !vocative_hits(txt).is_empty()
    }
}
