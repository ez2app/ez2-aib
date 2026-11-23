//! The line diff a persona write-back proposal shows (DESIGN-persona-propose-osverify-c2f0b-1123
//! §1b–§1c): aibd works out, from the card as it is and the text a seat proposed, which lines go
//! and which come; the page only draws it, the fingerprint window shows the counts and
//! [`diff_sha`].
//!
//! - Myers' shortest edit script on lines, written here (no new dependency). Lines keep their
//!   `\n` when compared, so a change in the last line's ending is a change.
//! - Bounded: at most [`MAX_LINES`] lines a side and [`MAX_EDIT`] lines changed; more is
//!   [`DiffError::TooBig`] (a write-back is a few lines, a whole new card is the persona update
//!   act's job).
//! - [`hidden_char_line`]: a line the proposal adds with a character the diff would not show
//!   (direction marks, zero-width, controls) is refused, not cleaned, so what is shown is what
//!   is written. Lines the card already had are not checked.

use sha2::{Digest, Sha256};

/// The most lines either side may have.
pub const MAX_LINES: usize = 2000;
/// The most lines added plus removed.
pub const MAX_EDIT: usize = 2000;

/// What happens to one line.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Op {
    Keep,
    Add,
    Del,
}

impl Op {
    /// The word the page gets.
    pub fn word(self) -> &'static str {
        match self {
            Op::Keep => "keep",
            Op::Add => "add",
            Op::Del => "del",
        }
    }
}

/// One line of the diff; numbers count from 1.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Line {
    pub op: Op,
    pub old_no: Option<usize>,
    pub new_no: Option<usize>,
    /// The line without its line ending.
    pub text: String,
}

/// The whole diff, in order.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Diff {
    pub lines: Vec<Line>,
    pub added: usize,
    pub removed: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum DiffError {
    #[error("over {MAX_LINES} lines or {MAX_EDIT} changed lines")]
    TooBig,
}

impl DiffError {
    pub fn reason(&self) -> &'static str {
        "persona_diff_too_big"
    }
}

fn split(s: &str) -> Vec<&str> {
    s.split_inclusive('\n').collect()
}

fn shown(line: &str) -> String {
    let t = line.strip_suffix('\n').unwrap_or(line);
    t.strip_suffix('\r').unwrap_or(t).to_owned()
}

/// The diff from `old` to `new`.
pub fn diff(old: &str, new: &str) -> Result<Diff, DiffError> {
    let a = split(old);
    let b = split(new);
    if a.len() > MAX_LINES || b.len() > MAX_LINES {
        return Err(DiffError::TooBig);
    }
    let script = edit_script(&a, &b).ok_or(DiffError::TooBig)?;
    let (mut i, mut j) = (0usize, 0usize);
    let mut lines = Vec::with_capacity(script.len());
    let (mut added, mut removed) = (0, 0);
    for op in script {
        match op {
            Op::Keep => {
                lines.push(Line {
                    op,
                    old_no: Some(i + 1),
                    new_no: Some(j + 1),
                    text: shown(a[i]),
                });
                i += 1;
                j += 1;
            }
            Op::Del => {
                lines.push(Line {
                    op,
                    old_no: Some(i + 1),
                    new_no: None,
                    text: shown(a[i]),
                });
                i += 1;
                removed += 1;
            }
            Op::Add => {
                lines.push(Line {
                    op,
                    old_no: None,
                    new_no: Some(j + 1),
                    text: shown(b[j]),
                });
                j += 1;
                added += 1;
            }
        }
    }
    Ok(Diff {
        lines,
        added,
        removed,
    })
}

/// Myers' greedy shortest edit script (one `Op` per line), or None past [`MAX_EDIT`].
fn edit_script(a: &[&str], b: &[&str]) -> Option<Vec<Op>> {
    let n = a.len() as isize;
    let m = b.len() as isize;
    let limit = MAX_EDIT.min(a.len() + b.len()) as isize;
    let off = limit + 1;
    let mut v = vec![0isize; (2 * limit + 3) as usize];
    // trace[d] = v[-d..=d] after round d
    let mut trace: Vec<Vec<isize>> = Vec::new();
    let mut end = None;
    'rounds: for d in 0..=limit {
        let mut k = -d;
        while k <= d {
            let mut x =
                if k == -d || (k != d && v[(off + k - 1) as usize] < v[(off + k + 1) as usize]) {
                    v[(off + k + 1) as usize]
                } else {
                    v[(off + k - 1) as usize] + 1
                };
            let mut y = x - k;
            while x < n && y < m && a[x as usize] == b[y as usize] {
                x += 1;
                y += 1;
            }
            v[(off + k) as usize] = x;
            if x >= n && y >= m {
                trace.push(v[(off - d) as usize..=(off + d) as usize].to_vec());
                end = Some(d);
                break 'rounds;
            }
            k += 2;
        }
        trace.push(v[(off - d) as usize..=(off + d) as usize].to_vec());
    }
    let last = end?;
    let mut ops = Vec::new();
    let (mut x, mut y) = (n, m);
    for d in (1..=last).rev() {
        let prev = &trace[(d - 1) as usize];
        let at = |k: isize| prev[(k + d - 1) as usize];
        let k = x - y;
        let prev_k = if k == -d || (k != d && at(k - 1) < at(k + 1)) {
            k + 1
        } else {
            k - 1
        };
        let prev_x = at(prev_k);
        let prev_y = prev_x - prev_k;
        while x > prev_x && y > prev_y {
            ops.push(Op::Keep);
            x -= 1;
            y -= 1;
        }
        if x == prev_x {
            ops.push(Op::Add);
            y -= 1;
        } else {
            ops.push(Op::Del);
            x -= 1;
        }
    }
    while x > 0 && y > 0 {
        ops.push(Op::Keep);
        x -= 1;
        y -= 1;
    }
    ops.reverse();
    Some(ops)
}

/// A character an added line may not have: [`aib_approve::text::is_bad_char`], except a tab,
/// the full-width space (visible in Chinese text) and a `\r` right before the line's end.
fn hidden(c: char) -> bool {
    c != '\t' && c != '\u{3000}' && aib_approve::text::is_bad_char(c)
}

/// The first added line (its number in the new text) holding a character the diff would not
/// show; None when there is none.
pub fn hidden_char_line(d: &Diff) -> Option<usize> {
    d.lines
        .iter()
        .filter(|l| l.op == Op::Add)
        .find(|l| l.text.chars().any(hidden))
        .and_then(|l| l.new_no)
}

/// The hash both the page card and the fingerprint window print (12 characters of it): fixed
/// by the persona and the two whole texts' SHA-256, nothing else.
pub fn diff_sha(persona_uid: &str, base_sha: &str, new_sha: &str) -> String {
    let h = Sha256::digest(format!(
        "aibd/persona-diff/v1\n{persona_uid}\n{base_sha}\n{new_sha}\n"
    ));
    h.iter().map(|b| format!("{b:02x}")).collect()
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)] // test code: a panic is the failure report
    use super::*;

    fn ops(d: &Diff) -> String {
        d.lines
            .iter()
            .map(|l| match l.op {
                Op::Keep => ' ',
                Op::Add => '+',
                Op::Del => '-',
            })
            .collect()
    }

    /// Applying the diff's keep + add lines gives the new text; keep + del gives the old.
    fn sides(d: &Diff) -> (Vec<String>, Vec<String>) {
        let old = d
            .lines
            .iter()
            .filter(|l| l.op != Op::Add)
            .map(|l| l.text.clone())
            .collect();
        let new = d
            .lines
            .iter()
            .filter(|l| l.op != Op::Del)
            .map(|l| l.text.clone())
            .collect();
        (old, new)
    }

    #[test]
    fn same_text_is_all_keep() {
        let d = diff("a\nb\n", "a\nb\n").unwrap();
        assert_eq!(ops(&d), "  ");
        assert_eq!((d.added, d.removed), (0, 0));
    }

    #[test]
    fn one_line_added_in_the_middle() {
        let d = diff("a\nb\nc\n", "a\nb\nx\nc\n").unwrap();
        assert_eq!(ops(&d), "  + ");
        assert_eq!((d.added, d.removed), (1, 0));
        assert_eq!(d.lines[2].new_no, Some(3));
        assert_eq!(d.lines[2].text, "x");
        assert_eq!(d.lines[3].old_no, Some(3));
    }

    #[test]
    fn a_changed_line_is_one_out_one_in() {
        let d = diff("a\nb\nc\n", "a\nB\nc\n").unwrap();
        assert_eq!((d.added, d.removed), (1, 1));
        let (old, new) = sides(&d);
        assert_eq!(old, ["a", "b", "c"]);
        assert_eq!(new, ["a", "B", "c"]);
    }

    #[test]
    fn the_last_line_ending_counts() {
        let d = diff("a\nb", "a\nb\n").unwrap();
        assert_eq!((d.added, d.removed), (1, 1));
    }

    #[test]
    fn empty_sides() {
        assert_eq!(diff("", "").unwrap().lines.len(), 0);
        let d = diff("", "a\nb\n").unwrap();
        assert_eq!(ops(&d), "++");
        let d = diff("a\nb\n", "").unwrap();
        assert_eq!(ops(&d), "--");
    }

    #[test]
    fn both_sides_come_back_from_a_mixed_diff() {
        let old = "人格\n說明一\n說明二\n規則 A\n規則 B\n結尾\n";
        let new = "人格\n說明一改\n規則 A\n規則 C\n規則 B\n新的結尾\n";
        let d = diff(old, new).unwrap();
        let (o, n) = sides(&d);
        assert_eq!(o.join("\n") + "\n", old);
        assert_eq!(n.join("\n") + "\n", new);
        assert_eq!(d.added, 3);
        assert_eq!(d.removed, 3);
    }

    #[test]
    fn too_many_lines_is_refused() {
        let big: String = "x\n".repeat(MAX_LINES + 1);
        assert_eq!(diff(&big, "x\n"), Err(DiffError::TooBig));
        assert_eq!(diff("x\n", &big), Err(DiffError::TooBig));
    }

    #[test]
    fn too_many_changes_is_refused() {
        let old: String = (0..MAX_LINES).map(|i| format!("a{i}\n")).collect();
        let new: String = (0..MAX_LINES).map(|i| format!("b{i}\n")).collect();
        // 2000 out + 2000 in = 4000 > MAX_EDIT
        assert_eq!(diff(&old, &new), Err(DiffError::TooBig));
    }

    #[test]
    fn a_hidden_character_on_an_added_line_is_found() {
        for bad in ['\u{202e}', '\u{200b}', '\u{0007}'] {
            let d = diff("a\n", &format!("a\nb{bad}c\n")).unwrap();
            assert_eq!(hidden_char_line(&d), Some(2), "{:?}", bad);
        }
    }

    #[test]
    fn hidden_characters_the_card_already_had_do_not_count() {
        let d = diff("a\u{200b}\n", "a\u{200b}\nb\n").unwrap();
        assert_eq!(hidden_char_line(&d), None);
    }

    #[test]
    fn tab_full_width_space_and_crlf_are_fine() {
        let d = diff("a\r\n", "a\r\n\tb\u{3000}c\r\n").unwrap();
        assert_eq!(hidden_char_line(&d), None);
        assert_eq!(d.lines[1].text, "\tb\u{3000}c");
    }

    #[test]
    fn diff_sha_depends_on_every_part() {
        let s = diff_sha("lvkson", "aa", "bb");
        assert_eq!(s.len(), 64);
        assert_ne!(s, diff_sha("lvksoN", "aa", "bb"));
        assert_ne!(s, diff_sha("lvkson", "ab", "bb"));
        assert_ne!(s, diff_sha("lvkson", "aa", "ba"));
        assert_eq!(s, diff_sha("lvkson", "aa", "bb"));
    }
}
