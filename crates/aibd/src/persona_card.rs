//! A1 (DESIGN-a1-edit-archive-08de5-1123 r0): a persona card's text as the page reads and edits
//! it — the name in its front matter (and a first `# ` title when there is one) and three
//! sections, `## 職責` / `## 紅線` / `## 知識域`. Text only: no file is read or written here
//! (writing is `crate::persona_write::write`). Every other byte of the card stays as it was.
//!
//! The real card's shape (11-23, un0eba 角色.md): front matter with `name: "…"` quoted, no `# `
//! title, then `## 職責`, `## 紅線`, `## 知識域`, `## 寫作規則(12 維)`.

/// The three sections the page edits: (field, heading).
pub const SECTIONS: [(&str, &str); 3] = [
    ("duty", "職責"),
    ("redlines", "紅線"),
    ("knowledge", "知識域"),
];
/// At most this many characters a section.
pub const SECTION_MAX: usize = 4000;
/// At most this many characters a name.
pub const NAME_MAX: usize = 40;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EditError {
    /// A section's text is longer than [`SECTION_MAX`], or a name than [`NAME_MAX`] / empty.
    TooLong(&'static str),
    /// A section's text has a line starting `## `: it would end the section early.
    Heading(&'static str),
    /// The card has no front matter `name:` to change.
    NoName,
}

impl EditError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::TooLong(_) => "too_long",
            Self::Heading(_) => "section_heading",
            Self::NoName => "card_has_no_name",
        }
    }
}

fn heading_line(l: &str) -> Option<&str> {
    l.strip_prefix("## ")
        .map(|h| h.trim_end_matches(['\r', '\n']).trim())
}

/// Where a section's body is: `(start, end)` byte offsets of the lines after `## <heading>` up
/// to the next `## ` line or the end; `None` when there is no such section.
fn section_span(text: &str, heading: &str) -> Option<(usize, usize)> {
    let mut at = 0;
    let mut start = None;
    for l in text.split_inclusive('\n') {
        if let Some(h) = heading_line(l) {
            if let Some(s) = start {
                return Some((s, at));
            }
            if h == heading {
                start = Some(at + l.len());
            }
        }
        at += l.len();
    }
    start.map(|s| (s, text.len()))
}

/// A section's text as written, without the blank lines around it (`""` when absent).
pub fn section(text: &str, heading: &str) -> String {
    section_span(text, heading)
        .map(|(s, e)| text[s..e].trim_matches(['\n', '\r']).to_owned())
        .unwrap_or_default()
}

/// f163c REVIEW-a1 r0 L1 (as the page refuses): a line that is a Markdown heading (`#` to
/// `######` then a space, after any spaces) or a `---` rule / front-matter line.
fn starts_a_heading(l: &str) -> bool {
    let t = l.trim_start();
    let hashes = t.bytes().take_while(|b| *b == b'#').count();
    ((1..=6).contains(&hashes) && t[hashes..].starts_with([' ', '\t'])) || t.trim_end() == "---"
}

/// What the page may send for a section: control characters out (line ends kept), within
/// [`SECTION_MAX`], no line that would start a new section.
pub fn clean_section(field: &'static str, body: &str) -> Result<String, EditError> {
    let b: String = body
        .replace("\r\n", "\n")
        .chars()
        .filter(|c| *c == '\n' || *c == '\t' || !c.is_control())
        .collect();
    if b.chars().count() > SECTION_MAX {
        return Err(EditError::TooLong(field));
    }
    if b.lines().any(starts_a_heading) {
        return Err(EditError::Heading(field));
    }
    Ok(b.trim_matches('\n').to_owned())
}

/// The card with `## <heading>`'s body replaced by `body` (the section added at the end when
/// there is none). The heading line and everything outside the body stay.
pub fn with_section(text: &str, heading: &str, body: &str) -> String {
    match section_span(text, heading) {
        Some((s, e)) => {
            let tail = if e == text.len() { "\n" } else { "\n\n" };
            format!("{}{body}{tail}{}", &text[..s], &text[e..])
        }
        None => {
            let sep = if text.ends_with("\n\n") || text.is_empty() {
                ""
            } else if text.ends_with('\n') {
                "\n"
            } else {
                "\n\n"
            };
            format!("{text}{sep}## {heading}\n{body}\n")
        }
    }
}

/// A name as the page may send it: one line, no control characters, 1–[`NAME_MAX`] characters.
pub fn clean_name(name: &str) -> Result<String, EditError> {
    let n: String = name.chars().filter(|c| !c.is_control()).collect();
    let n = n.trim();
    if n.is_empty() || n.chars().count() > NAME_MAX {
        return Err(EditError::TooLong("name"));
    }
    Ok(n.to_owned())
}

/// The card with its front matter `name:` set to `name` (written quoted, `"` and `\` escaped),
/// and a first `# ` title after the front matter, when there is one, too.
pub fn with_name(text: &str, name: &str) -> Result<String, EditError> {
    let front = crate::persona_write::front_block(text).ok_or(EditError::NoName)?;
    let quoted = format!("\"{}\"", name.replace('\\', "\\\\").replace('"', "\\\""));
    let mut out = String::with_capacity(text.len() + name.len());
    let mut named = false;
    for l in front.split_inclusive('\n') {
        match l.split_once(':') {
            Some((k, _)) if !named && k.trim() == "name" && !k.starts_with(' ') => {
                let end = if l.ends_with("\r\n") {
                    "\r\n"
                } else if l.ends_with('\n') {
                    "\n"
                } else {
                    ""
                };
                out.push_str(&format!("name: {quoted}{end}"));
                named = true;
            }
            _ => out.push_str(l),
        }
    }
    if !named {
        return Err(EditError::NoName);
    }
    let mut titled = false;
    for l in text[front.len()..].split_inclusive('\n') {
        if !titled && l.starts_with("# ") {
            let end = if l.ends_with('\n') { "\n" } else { "" };
            out.push_str(&format!("# {name}{end}"));
            titled = true;
        } else {
            if heading_line(l).is_some() {
                titled = true; // a title comes before the first section, or not at all
            }
            out.push_str(l);
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    /// The real card's shape, cut down.
    const CARD: &str = "---\nuid: \"un0eba\"\nname: \"AIB2主線核心架構師\"\ncode: aib2core\ntools: []\n---\n## 職責\n寫 aibd。\n\n## 紅線\n不 push。\n不合 main。\n\n## 知識域\nRust。\n\n## 寫作規則(12 維)\n- 短句\n";

    /// A1-1: the three sections read as written.
    #[test]
    fn the_three_sections_read_as_written() {
        assert_eq!(section(CARD, "職責"), "寫 aibd。");
        assert_eq!(section(CARD, "紅線"), "不 push。\n不合 main。");
        assert_eq!(section(CARD, "知識域"), "Rust。");
        assert_eq!(section(CARD, "沒有這段"), "");
    }

    /// A1-1: a section's body is replaced and nothing else moves; a missing one is added last.
    #[test]
    fn a_section_is_replaced_and_nothing_else_moves() {
        let t = with_section(CARD, "紅線", "不 push。\n不換 aibd。");
        assert_eq!(section(&t, "紅線"), "不 push。\n不換 aibd。");
        assert_eq!(
            section(&t, "職責"),
            "寫 aibd。",
            "reason: other_section_moved"
        );
        assert_eq!(
            section(&t, "知識域"),
            "Rust。",
            "reason: section_ate_the_next"
        );
        assert!(t.ends_with("## 寫作規則(12 維)\n- 短句\n"), "{t}");
        assert_eq!(with_section(CARD, "紅線", &section(CARD, "紅線")), CARD);
        let bare = "---\nname: x\n---\n## 職責\na\n";
        let t = with_section(bare, "知識域", "b");
        assert_eq!(t, "---\nname: x\n---\n## 職責\na\n\n## 知識域\nb\n");
        let t = with_section(bare, "職責", "c");
        assert_eq!(t, "---\nname: x\n---\n## 職責\nc\n");
    }

    /// A1-5: a body cannot start a section of its own, nor be too long.
    #[test]
    fn a_body_cannot_start_a_section_or_run_long() {
        assert_eq!(
            clean_section("duty", "a\n## 紅線\nb"),
            Err(EditError::Heading("duty")),
            "reason: heading_let_in"
        );
        assert_eq!(
            clean_section("duty", "a\n### 小節"),
            Err(EditError::Heading("duty")),
            "reason: subheading_let_in"
        );
        assert_eq!(
            clean_section("duty", "#標籤\n-- 兩槓").unwrap(),
            "#標籤\n-- 兩槓"
        );
        assert_eq!(
            clean_section("duty", &"字".repeat(SECTION_MAX + 1)),
            Err(EditError::TooLong("duty"))
        );
        assert_eq!(clean_section("duty", "a\u{7}b\r\nc").unwrap(), "ab\nc");
    }

    /// A1-1: the name is changed in the front matter (and a title when there is one) only.
    #[test]
    fn the_name_changes_where_it_is_written_only() {
        let t = with_name(CARD, "新\"名字").unwrap();
        assert!(t.contains("\nname: \"新\\\"名字\"\n"), "{t}");
        assert_eq!(t.matches("name:").count(), 1);
        assert_eq!(
            t.replace("name: \"新\\\"名字\"", "name: \"AIB2主線核心架構師\""),
            CARD,
            "reason: other_bytes_moved"
        );
        let titled = "---\nname: a\n---\n# a\n\n## 職責\n# not a title\n";
        let t = with_name(titled, "b").unwrap();
        assert_eq!(t, "---\nname: \"b\"\n---\n# b\n\n## 職責\n# not a title\n");
        assert_eq!(with_name("## 職責\n", "b"), Err(EditError::NoName));
        assert_eq!(
            clean_name(&"名".repeat(NAME_MAX + 1)),
            Err(EditError::TooLong("name"))
        );
        assert_eq!(clean_name("  a\u{1b}b  ").unwrap(), "ab");
    }
}
