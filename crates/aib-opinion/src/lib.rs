//! aib-opinion: council opinions in a fixed format, and where the final decision put each item
//! (S71, DEV-S71-opinion-format-043dd-1116 r0; Andy 11-14 09:4x, DEV-shadow-2x §9).
//!
//! An opinion file keeps the S77 template's first line (`結論: … H/M/L: a/b/c`) and writes each
//! item on one line:
//!
//! ```text
//! - O1 [H] §4.1 | merging "at once" contradicts the 2-hour rule → A only records a candidate
//! ```
//!
//! The final decision cites an item as `<seat>-O<n>` anywhere on a line. The item lands under the
//! nearest heading above that line; a line that also says 不採 lands it as not taken. Items the
//! final never cites are listed, so a coordinator cannot drop one by forgetting to copy it.
//!
//! Only reads text. It does not judge what an opinion means or whether the final agrees with it.

use serde_json::{json, Value};

/// How much an item weighs (the review scale: H severe, M medium, L light, N a note).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Sev {
    H,
    M,
    L,
    N,
}

impl Sev {
    pub fn code(self) -> &'static str {
        match self {
            Sev::H => "H",
            Sev::M => "M",
            Sev::L => "L",
            Sev::N => "N",
        }
    }
    fn parse(s: &str) -> Option<Sev> {
        match s {
            "H" => Some(Sev::H),
            "M" => Some(Sev::M),
            "L" => Some(Sev::L),
            "N" => Some(Sev::N),
            _ => None,
        }
    }
    /// H and M say what to change; L and N may leave the fix out.
    fn needs_fix(self) -> bool {
        matches!(self, Sev::H | Sev::M)
    }
    /// N is a note (agree, nothing to change): the final does not have to place it.
    fn needs_landing(self) -> bool {
        !matches!(self, Sev::N)
    }
}

/// One opinion item.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Item {
    pub seat: String,
    pub n: u32,
    pub sev: Sev,
    pub section: String,
    pub text: String,
    pub fix: Option<String>,
    /// 1-based line in the opinion file.
    pub line: usize,
}

impl Item {
    /// How the final cites it: `1ced7-O3`.
    pub fn cite(&self) -> String {
        format!("{}-O{}", self.seat, self.n)
    }
}

/// A format problem at a line (0 = the file as a whole).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Problem {
    pub line: usize,
    pub msg: String,
}

/// One parsed opinion file.
#[derive(Clone, Debug)]
pub struct Opinion {
    pub seat: String,
    pub items: Vec<Item>,
    pub problems: Vec<Problem>,
}

/// The seat of an opinion: `owner:` in the leading `---` header, else `<seat>` in
/// `OPINION-<topic>-<seat>-<date>.md` (the topic may hold dashes, so it is the part before the
/// date). None when neither gives one.
pub fn seat_of(file_name: &str, text: &str) -> Option<String> {
    let mut lines = text.lines().map(|l| l.trim_end_matches('\r'));
    if lines.next() == Some("---") {
        for l in lines {
            if l == "---" {
                break;
            }
            if let Some(v) = l.strip_prefix("owner:") {
                let v = v.trim();
                if is_seat(v) {
                    return Some(v.to_string());
                }
            }
        }
    }
    let stem = file_name.rsplit(['/', '\\']).next()?;
    let stem = stem.strip_suffix(".md").unwrap_or(stem);
    let parts: Vec<&str> = stem.split('-').collect();
    if parts.len() >= 4 && parts[0] == "OPINION" {
        let seat = parts[parts.len() - 2];
        if is_seat(seat) {
            return Some(seat.to_string());
        }
    }
    None
}

/// A seat code as the final writes it: lower-case letters and digits, at least 3 (five-hex seats,
/// `codex`). Shorter runs are too easy to hit by accident in ordinary text.
fn is_seat(s: &str) -> bool {
    s.len() >= 3
        && s.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit())
}

/// Read one opinion file. Problems are collected, not fatal, so `check` can list them all.
pub fn parse(file_name: &str, text: &str) -> Opinion {
    let mut problems = Vec::new();
    let seat = match seat_of(file_name, text) {
        Some(s) => s,
        None => {
            problems.push(Problem {
                line: 0,
                msg: "no seat: write `owner: <seat>` in the --- header or name the file OPINION-<topic>-<seat>-<date>.md".into(),
            });
            String::from("?")
        }
    };
    let mut items: Vec<Item> = Vec::new();
    let mut declared: Option<(usize, [u32; 3])> = None;
    for (i, raw) in text.lines().enumerate() {
        let line = i + 1;
        let l = raw.trim_end_matches('\r');
        if declared.is_none() {
            if let Some(rest) = l.strip_prefix("結論:") {
                match counts_of(rest) {
                    Some(c) => declared = Some((line, c)),
                    None => problems.push(Problem {
                        line,
                        msg: "the 結論 line needs `H/M/L: a/b/c`".into(),
                    }),
                }
                continue;
            }
        }
        let t = l.trim_start();
        let Some(body) = t.strip_prefix("- O") else {
            continue;
        };
        if !body.starts_with(|c: char| c.is_ascii_digit()) {
            continue;
        }
        match item_of(&seat, body, line) {
            Ok(it) => {
                if items.iter().any(|x| x.n == it.n) {
                    problems.push(Problem {
                        line,
                        msg: format!("O{} is used twice in this file", it.n),
                    });
                } else {
                    items.push(it);
                }
            }
            Err(msg) => problems.push(Problem { line, msg }),
        }
    }
    match declared {
        None => problems.push(Problem {
            line: 0,
            msg: "no `結論: … H/M/L: a/b/c` line".into(),
        }),
        Some((line, [h, m, l])) => {
            let count = |s: Sev| items.iter().filter(|i| i.sev == s).count() as u32;
            let got = [count(Sev::H), count(Sev::M), count(Sev::L)];
            if got != [h, m, l] {
                problems.push(Problem {
                    line,
                    msg: format!(
                        "結論 says H/M/L {h}/{m}/{l} but the items are {}/{}/{}",
                        got[0], got[1], got[2]
                    ),
                });
            }
        }
    }
    Opinion {
        seat,
        items,
        problems,
    }
}

/// `… H/M/L: 1/2/0` → [1, 2, 0]. ASCII digits only.
fn counts_of(rest: &str) -> Option<[u32; 3]> {
    let (_, after) = rest.split_once("H/M/L:")?;
    let nums = after.split_whitespace().next()?;
    let mut it = nums.split('/');
    let mut out = [0u32; 3];
    for slot in &mut out {
        let s = it.next()?;
        if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        *slot = s.parse().ok()?;
    }
    if it.next().is_some() {
        return None;
    }
    Some(out)
}

/// The part after `- O`: `<n> [S] <section> | <text> → <fix>`.
fn item_of(seat: &str, body: &str, line: usize) -> Result<Item, String> {
    let digits: String = body.chars().take_while(|c| c.is_ascii_digit()).collect();
    let n: u32 = digits
        .parse()
        .map_err(|_| format!("O{digits}: the number is too large"))?;
    if n == 0 {
        return Err("O0: items count from O1".into());
    }
    let rest = &body[digits.len()..];
    let rest = rest
        .strip_prefix(" [")
        .ok_or_else(|| format!("O{n}: write ` [H|M|L|N] ` after the number"))?;
    let (sev, rest) = rest
        .split_once("] ")
        .ok_or_else(|| format!("O{n}: write ` [H|M|L|N] ` after the number"))?;
    let sev = Sev::parse(sev).ok_or_else(|| format!("O{n}: [{sev}] is not H, M, L or N"))?;
    let (section, rest) = rest
        .split_once(" | ")
        .ok_or_else(|| format!("O{n}: write `<section> | <opinion>` (a space each side of |)"))?;
    let section = section.trim();
    if section.is_empty() {
        return Err(format!("O{n}: the section is empty (§4.1, 題 3, A …)"));
    }
    let (text, fix) = match rest.split_once('→') {
        Some((t, f)) => (t.trim(), Some(f.trim())),
        None => (rest.trim(), None),
    };
    if text.is_empty() {
        return Err(format!("O{n}: the opinion is empty"));
    }
    let fix = fix.filter(|f| !f.is_empty()).map(str::to_string);
    if sev.needs_fix() && fix.is_none() {
        return Err(format!("O{n}: [{}] needs `→ <fix>`", sev.code()));
    }
    Ok(Item {
        seat: seat.to_string(),
        n,
        sev,
        section: section.to_string(),
        text: text.to_string(),
        fix,
        line,
    })
}

/// Where the final cites an item.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Landing {
    /// The nearest heading above, without its #s; empty when none.
    pub heading: String,
    /// 1-based line in the final.
    pub line: usize,
    /// The citing line says 不採.
    pub not_taken: bool,
}

/// A citation `<seat>-O<n>` found on one line.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Cite {
    pub seat: String,
    pub n: u32,
}

/// Every `<seat>-O<n>` on a line: the seat is a run of at least 3 lower-case letters or digits
/// that does not continue a longer word, the number does not run into a letter or digit.
pub fn cites_in(line: &str) -> Vec<Cite> {
    let b = line.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i + 2 < b.len() {
        if b[i] == b'-' && b[i + 1] == b'O' && b[i + 2].is_ascii_digit() {
            let mut s = i;
            while s > 0 && (b[s - 1].is_ascii_lowercase() || b[s - 1].is_ascii_digit()) {
                s -= 1;
            }
            let word_before = s > 0 && (b[s - 1].is_ascii_alphanumeric() || b[s - 1] == b'_');
            let mut e = i + 2;
            while e < b.len() && b[e].is_ascii_digit() {
                e += 1;
            }
            let word_after = e < b.len() && (b[e].is_ascii_alphanumeric() || b[e] == b'_');
            let seat = &line[s..i];
            if is_seat(seat) && !word_before && !word_after {
                if let Ok(n) = line[i + 2..e].parse() {
                    out.push(Cite {
                        seat: seat.to_string(),
                        n,
                    });
                }
            }
            i = e;
        } else {
            i += 1;
        }
    }
    out
}

/// A Markdown heading (`#` to `####` then a space) → its text.
fn heading_of(line: &str) -> Option<&str> {
    let hashes = line.bytes().take_while(|&b| b == b'#').count();
    if (1..=4).contains(&hashes) {
        line[hashes..].strip_prefix(' ').map(str::trim)
    } else {
        None
    }
}

/// The landing table.
#[derive(Clone, Debug)]
pub struct Table {
    /// Items in seat then number order, each with every place the final cites it.
    pub rows: Vec<(Item, Vec<Landing>)>,
    /// Citations in the final that match no item (a typo, or an item that was renumbered).
    pub unknown: Vec<(String, usize)>,
}

impl Table {
    /// Items that must land and do not (H / M / L; N is a note).
    pub fn unlanded(&self) -> Vec<&Item> {
        self.rows
            .iter()
            .filter(|(i, l)| i.sev.needs_landing() && l.is_empty())
            .map(|(i, _)| i)
            .collect()
    }
}

/// Find each item's citations in the final.
pub fn table(final_text: &str, opinions: &[Opinion]) -> Table {
    let mut rows: Vec<(Item, Vec<Landing>)> = opinions
        .iter()
        .flat_map(|o| o.items.iter().cloned())
        .map(|i| (i, Vec::new()))
        .collect();
    rows.sort_by(|a, b| (&a.0.seat, a.0.n).cmp(&(&b.0.seat, b.0.n)));
    let mut unknown = Vec::new();
    let mut heading = String::new();
    for (i, raw) in final_text.lines().enumerate() {
        let line = i + 1;
        let l = raw.trim_end_matches('\r');
        if let Some(h) = heading_of(l) {
            heading = h.to_string();
        }
        let not_taken = l.contains("不採");
        for c in cites_in(l) {
            match rows
                .iter_mut()
                .find(|(it, _)| it.seat == c.seat && it.n == c.n)
            {
                Some((_, lands)) => lands.push(Landing {
                    heading: heading.clone(),
                    line,
                    not_taken,
                }),
                None => unknown.push((format!("{}-O{}", c.seat, c.n), line)),
            }
        }
    }
    Table { rows, unknown }
}

fn cell(s: &str) -> String {
    s.replace('|', "\\|")
}

fn landing_text(l: &Landing) -> String {
    let h = if l.heading.is_empty() {
        "(no heading)".to_string()
    } else {
        l.heading.clone()
    };
    if l.not_taken {
        format!("不採納:{h}(第 {} 行)", l.line)
    } else {
        format!("{h}(第 {} 行)", l.line)
    }
}

/// The table as Markdown, then 未落點 and 引用不到.
pub fn render_md(t: &Table) -> String {
    let mut s =
        String::from("| 來源 | 條 | 嚴重度 | 章節 | 意見 | 落點 |\n|---|---|---|---|---|---|\n");
    for (i, lands) in &t.rows {
        let opinion = match &i.fix {
            Some(f) => format!("{} → {}", i.text, f),
            None => i.text.clone(),
        };
        let place = if lands.is_empty() {
            if i.sev.needs_landing() {
                "**未落點**".to_string()
            } else {
                "—".to_string()
            }
        } else {
            lands.iter().map(landing_text).collect::<Vec<_>>().join(";")
        };
        s.push_str(&format!(
            "| {} | O{} | {} | {} | {} | {} |\n",
            cell(&i.seat),
            i.n,
            i.sev.code(),
            cell(&i.section),
            cell(&opinion),
            cell(&place)
        ));
    }
    let un = t.unlanded();
    if un.is_empty() {
        s.push_str("\n未落點:無\n");
    } else {
        s.push_str(&format!("\n未落點:{} 條\n", un.len()));
        for i in un {
            s.push_str(&format!(
                "- {} [{}] {} | {}\n",
                i.cite(),
                i.sev.code(),
                i.section,
                i.text
            ));
        }
    }
    if !t.unknown.is_empty() {
        s.push_str(&format!("\n引用不到:{} 處\n", t.unknown.len()));
        for (c, line) in &t.unknown {
            s.push_str(&format!("- 第 {line} 行 {c}\n"));
        }
    }
    s
}

/// The table as JSON.
pub fn to_json(t: &Table) -> Value {
    json!({
        "rows": t.rows.iter().map(|(i, lands)| json!({
            "cite": i.cite(),
            "seat": i.seat,
            "n": i.n,
            "sev": i.sev.code(),
            "section": i.section,
            "text": i.text,
            "fix": i.fix,
            "line": i.line,
            "landings": lands.iter().map(|l| json!({
                "heading": l.heading,
                "line": l.line,
                "not_taken": l.not_taken,
            })).collect::<Vec<_>>(),
        })).collect::<Vec<_>>(),
        "unlanded": t.unlanded().iter().map(|i| i.cite()).collect::<Vec<_>>(),
        "unknown": t.unknown.iter().map(|(c, line)| json!({"cite": c, "line": line})).collect::<Vec<_>>(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const GOOD: &str = "---\nstatus: 意見\nowner: 1ced7\n---\n# title\n結論: 有條件 H/M/L: 1/1/1\n題目 sha: 1a2b3c4d\n## 第 1 題\n- O1 [H] §4.1 | at once contradicts the 2-hour rule → A only records a candidate\n- O2 [M] B | do not clean the tree → clean target only\n- O3 [L] 題 3 | order is fine but E first\n- O4 [N] 題 3 | agree\n";

    fn probs(o: &Opinion) -> Vec<String> {
        o.problems
            .iter()
            .map(|p| format!("{}: {}", p.line, p.msg))
            .collect()
    }

    #[test]
    fn a_well_formed_opinion_parses_every_item() {
        let o = parse("x.md", GOOD);
        assert_eq!(probs(&o), Vec::<String>::new());
        assert_eq!(o.seat, "1ced7");
        assert_eq!(o.items.len(), 4);
        let i = &o.items[0];
        assert_eq!((i.n, i.sev, i.section.as_str()), (1, Sev::H, "§4.1"));
        assert_eq!(i.text, "at once contradicts the 2-hour rule");
        assert_eq!(i.fix.as_deref(), Some("A only records a candidate"));
        assert_eq!(i.line, 9);
        assert_eq!(o.items[2].fix, None);
        assert_eq!(o.items[3].cite(), "1ced7-O4");
    }

    #[test]
    fn crlf_line_ends_read_the_same() {
        let o = parse("x.md", &GOOD.replace('\n', "\r\n"));
        assert_eq!(probs(&o), Vec::<String>::new());
        assert_eq!(o.items[1].fix.as_deref(), Some("clean target only"));
    }

    #[test]
    fn the_seat_comes_from_the_header_else_from_the_file_name() {
        assert_eq!(
            seat_of("OPINION-a-b-ebb36-1115.md", GOOD).as_deref(),
            Some("1ced7")
        );
        let bare = "結論: 同意 H/M/L: 0/0/0\n";
        assert_eq!(
            seat_of("/p/OPINION-unblock-loop-codex-1115.md", bare).as_deref(),
            Some("codex")
        );
        assert_eq!(
            seat_of("C:\\p\\OPINION-x-ebb36-1115.md", bare).as_deref(),
            Some("ebb36")
        );
        assert_eq!(seat_of("notes.md", bare), None);
        let o = parse("notes.md", bare);
        assert_eq!(o.problems[0].line, 0);
        assert!(o.problems[0].msg.starts_with("no seat"), "{:?}", o.problems);
    }

    #[test]
    fn the_declared_counts_must_match_the_items() {
        let o = parse("x.md", &GOOD.replace("H/M/L: 1/1/1", "H/M/L: 1/0/1"));
        assert_eq!(
            probs(&o),
            vec!["6: 結論 says H/M/L 1/0/1 but the items are 1/1/1".to_string()]
        );
        let o = parse("x.md", &GOOD.replace("結論: 有條件 H/M/L: 1/1/1\n", ""));
        assert_eq!(
            probs(&o),
            vec!["0: no `結論: … H/M/L: a/b/c` line".to_string()]
        );
        let o = parse("x.md", &GOOD.replace("H/M/L: 1/1/1", "H/M/L: 1/１/1"));
        assert_eq!(probs(&o)[0], "6: the 結論 line needs `H/M/L: a/b/c`");
        let o = parse("x.md", &GOOD.replace("H/M/L: 1/1/1", "H/M/L: +1/1/1"));
        assert_eq!(
            probs(&o)[0],
            "6: the 結論 line needs `H/M/L: a/b/c`",
            "a sign is not a digit"
        );
    }

    #[test]
    fn each_malformed_item_line_is_named() {
        let cases = [
            (
                "- O5 H §1 | x → y",
                "O5: write ` [H|M|L|N] ` after the number",
            ),
            ("- O5 [X] §1 | x → y", "O5: [X] is not H, M, L or N"),
            (
                "- O5 [M] §1 x → y",
                "O5: write `<section> | <opinion>` (a space each side of |)",
            ),
            (
                "- O5 [M]   | x → y",
                "O5: the section is empty (§4.1, 題 3, A …)",
            ),
            ("- O5 [M] §1 | x", "O5: [M] needs `→ <fix>`"),
            ("- O5 [H] §1 | x →  ", "O5: [H] needs `→ <fix>`"),
            ("- O5 [L] §1 |  → y", "O5: the opinion is empty"),
            ("- O0 [L] §1 | x", "O0: items count from O1"),
            ("- O1 [L] §1 | again", "O1 is used twice in this file"),
        ];
        for (line, want) in cases {
            let text = GOOD.replace("- O4 [N] 題 3 | agree\n", &format!("{line}\n"));
            let o = parse("x.md", &text);
            assert!(
                o.problems.iter().any(|p| p.line == 12 && p.msg == want),
                "{line}: {:?}",
                o.problems
            );
        }
    }

    #[test]
    fn lines_that_only_look_like_items_are_left_alone() {
        let text = format!("{GOOD}- Others say this\n- OK [H] not an item\nplain - O1 [H] x\n");
        let o = parse("x.md", &text);
        assert_eq!(probs(&o), Vec::<String>::new());
        assert_eq!(o.items.len(), 4);
    }

    #[test]
    fn citations_need_a_whole_seat_and_a_whole_number() {
        let c = |s: &str| {
            cites_in(s)
                .into_iter()
                .map(|c| format!("{}-O{}", c.seat, c.n))
                .collect::<Vec<_>>()
        };
        assert_eq!(
            c("收 1ced7-O1、codex-O12;ebb36-O3。"),
            ["1ced7-O1", "codex-O12", "ebb36-O3"]
        );
        assert_eq!(c("(1ced7-O2)"), ["1ced7-O2"]);
        assert!(c("ab-O1 is too short").is_empty());
        assert!(c("X1ced7-O1 runs on from a word").is_empty());
        assert!(c("1ced7-O1a runs into a letter").is_empty());
        assert!(c("1ced7-Ox no number").is_empty());
        assert!(c("ABCDE-O1 upper case").is_empty());
    }

    const FINAL: &str = "# 定稿\n## 一、掛點\n- A 只登記候選(1ced7-O1、ebb36-O1)\n### B\n- 不採 1ced7-O2:樹要留\n## 二、順序\n照原案 1ced7-O3;也見 1ced7-O9\n";

    fn two() -> Vec<Opinion> {
        let e = "結論: 同意 H/M/L: 0/1/0\n- O1 [M] A | same → only record\n- O2 [N] 題 1 | agree\n";
        vec![parse("x.md", GOOD), parse("OPINION-t-ebb36-1115.md", e)]
    }

    #[test]
    fn each_item_lands_under_the_heading_above_its_citation() {
        let t = table(FINAL, &two());
        let land = |cite: &str| {
            t.rows
                .iter()
                .find(|(i, _)| i.cite() == cite)
                .map(|(_, l)| l.clone())
                .unwrap()
        };
        assert_eq!(
            land("1ced7-O1"),
            [Landing {
                heading: "一、掛點".into(),
                line: 3,
                not_taken: false
            }]
        );
        assert_eq!(
            land("1ced7-O2"),
            [Landing {
                heading: "B".into(),
                line: 5,
                not_taken: true
            }]
        );
        assert_eq!(land("1ced7-O3")[0].heading, "二、順序");
        assert_eq!(land("ebb36-O1")[0].line, 3);
        assert!(land("1ced7-O4").is_empty());
        assert_eq!(t.unknown, [("1ced7-O9".to_string(), 7)]);
    }

    #[test]
    fn only_items_that_must_land_are_listed_as_unlanded() {
        let t = table(&FINAL.replace("ebb36-O1", "x"), &two());
        let un: Vec<String> = t.unlanded().iter().map(|i| i.cite()).collect();
        assert_eq!(
            un,
            ["ebb36-O1"],
            "N items (1ced7-O4, ebb36-O2) need no landing"
        );
        let md = render_md(&t);
        assert!(
            md.contains("| ebb36 | O1 | M | A | same → only record | **未落點** |"),
            "{md}"
        );
        assert!(md.contains("| 1ced7 | O4 | N | 題 3 | agree | — |"), "{md}");
        assert!(
            md.contains("\n未落點:1 條\n- ebb36-O1 [M] A | same\n"),
            "{md}"
        );
        assert!(md.contains("\n引用不到:1 處\n- 第 7 行 1ced7-O9\n"), "{md}");
    }

    #[test]
    fn rows_run_in_seat_then_number_order_and_cells_escape_bars() {
        let mut ops = two();
        ops.reverse();
        ops[1].items[0].text = "a | b".into();
        let t = table(FINAL, &ops);
        let order: Vec<String> = t.rows.iter().map(|(i, _)| i.cite()).collect();
        assert_eq!(
            order,
            ["1ced7-O1", "1ced7-O2", "1ced7-O3", "1ced7-O4", "ebb36-O1", "ebb36-O2"]
        );
        let md = render_md(&t);
        assert!(
            md.contains("| a \\| b → A only records a candidate |"),
            "{md}"
        );
        assert!(md.contains("不採納:B(第 5 行)"), "{md}");
        assert!(md.contains("\n未落點:無\n"), "{md}");
    }

    #[test]
    fn the_json_carries_every_row_and_both_lists() {
        let t = table(&FINAL.replace("ebb36-O1", "x"), &two());
        let j = to_json(&t);
        assert_eq!(j["rows"].as_array().unwrap().len(), 6);
        assert_eq!(j["rows"][1]["landings"][0]["not_taken"], true);
        assert_eq!(j["unlanded"], json!(["ebb36-O1"]));
        assert_eq!(j["unknown"], json!([{"cite": "1ced7-O9", "line": 7}]));
    }
}
