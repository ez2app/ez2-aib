//! Which `--evidence` closes a dm (evidence_kind / evidence_ref in 1.x).
//!
//! Taken: an existing file, a reply id this seat sent (found in some seat's queue with a
//! measured sender), a commit the repo knows, a full `https://github.com/ez2app/<repo>/
//! pull/N` or `/commit/<sha>` URL. When the whole string is not one of those, the first
//! full PR URL inside the text is taken as is; otherwise up to four reply-id / sha / path
//! candidates are checked in that order. `#N` alone and plain text are never evidence
//! (the caller may still close with `--why`, recorded as `done_kind = reason`).
//!
//! The checks that need the disk, git or other queues go through [`Probe`]; everything
//! else is pure.

use crate::util::{py_isspace, py_strip, py_word};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    File,
    Dm,
    Commit,
    Pr,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::File => "file",
            Kind::Dm => "dm",
            Kind::Commit => "commit",
            Kind::Pr => "pr",
        }
    }
}

/// The I/O the evidence rules need. Implementations decide how; this crate never touches
/// the disk itself.
pub trait Probe {
    /// `text` names an existing regular file (after `~` expansion).
    fn is_file(&self, text: &str) -> bool;
    /// `sha` is a commit the repo knows.
    fn commit_exists(&self, sha: &str) -> bool;
    /// Some seat's queue holds item `id`, sent by `seat` with a measured sender source.
    fn reply_sent_by(&self, seat: &str, id: &str) -> bool;
}

/// Sender sources that count as measured for reply-id evidence (MEASURED_SOURCES).
pub const MEASURED_SOURCES: &[&str] = &["pane_pid_chain", "bg_fork_chain"];
/// At most this many candidates inside a text are checked (_EV_SCAN_MAX).
pub const SCAN_MAX: usize = 4;

/// `q_` + 16 lowercase hex, the whole string (ITEM_RE).
pub fn is_item_id(s: &str) -> bool {
    s.len() == 18 && s.starts_with("q_") && s[2..].bytes().all(is_lower_hex)
}

fn is_lower_hex(b: u8) -> bool {
    b.is_ascii_digit() || (b'a'..=b'f').contains(&b)
}

fn is_sha(s: &str) -> bool {
    (7..=40).contains(&s.len()) && s.bytes().all(is_lower_hex)
}

const PR_HEAD: &str = "https://github.com/ez2app/";

/// Length of a PR / commit URL (_PR_CORE) starting at the beginning of `s`, without the
/// lookahead after it. None when `s` does not start with one.
fn pr_core_len(s: &str) -> Option<usize> {
    let rest = s.strip_prefix(PR_HEAD)?;
    let repo_len = rest
        .bytes()
        .take_while(|b| b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'-'))
        .count();
    let repo = &rest[..repo_len];
    // (?!\.?/) and (?![^/\s]*\.\.): the repo is neither empty nor "." and has no ".."
    if repo.is_empty() || repo == "." || repo.contains("..") {
        return None;
    }
    let after = rest[repo_len..].strip_prefix('/')?;
    // pull/<1-9 digits> or commit/<7-40 lowercase hex>
    let (tail, min, max, ok_byte): (&str, usize, usize, fn(u8) -> bool) =
        match after.strip_prefix("pull/") {
            Some(t) => (t, 1, 9, |b| b.is_ascii_digit()),
            None => (after.strip_prefix("commit/")?, 7, 40, is_lower_hex),
        };
    let n = tail.bytes().take_while(|b| ok_byte(*b)).count();
    if n < min || n > max {
        return None;
    }
    Some(s.len() - tail.len() + n)
}

/// The whole string is a PR / commit URL, an optional trailing "/" allowed (_PR_URL_RE).
pub fn is_pr_url(s: &str) -> bool {
    pr_core_len(s).is_some_and(|n| s[n..].is_empty() || &s[n..] == "/")
}

/// The first PR / commit URL inside `text` that is not glued to a word, "/" or "=" before
/// it and not followed by [0-9A-Za-z_] (_EV_PR_IN).
pub fn pr_url_in(text: &str) -> Option<&str> {
    let mut from = 0;
    while let Some(off) = text[from..].find(PR_HEAD) {
        let at = from + off;
        let glued = text[..at]
            .chars()
            .next_back()
            .is_some_and(|c| py_word(c) || c == '/' || c == '=');
        if !glued {
            if let Some(n) = pr_core_len(&text[at..]) {
                let next = text[at + n..].bytes().next();
                if !next.is_some_and(|b| b.is_ascii_alphanumeric() || b == b'_') {
                    return Some(&text[at..at + n]);
                }
            }
        }
        from = at + 1;
    }
    None
}

fn word_start(text: &str, at: usize) -> bool {
    !text[..at].chars().next_back().is_some_and(py_word)
}

fn word_end(text: &str, at: usize) -> bool {
    !text[at..].chars().next().is_some_and(py_word)
}

/// `\bq_[0-9a-f]{16}\b` matches, left to right.
fn item_ids_in(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut i = 0;
    while let Some(off) = text[i..].find("q_") {
        let at = i + off;
        let end = at + 18;
        if word_start(text, at)
            && end <= text.len()
            && text.is_char_boundary(end)
            && is_item_id(&text[at..end])
            && word_end(text, end)
        {
            out.push(&text[at..end]);
            i = end;
        } else {
            i = at + 1;
        }
    }
    out
}

/// `\b[0-9a-f]{7,40}\b` matches, left to right.
fn shas_in(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let b = text.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if is_lower_hex(b[i]) && word_start(text, i) {
            let n = b[i..].iter().take_while(|x| is_lower_hex(**x)).count();
            if (7..=40).contains(&n) && word_end(text, i + n) {
                out.push(&text[i..i + n]);
            }
            i += n;
        } else {
            i += 1;
        }
    }
    out
}

fn path_stop(c: char) -> bool {
    py_isspace(c)
        || matches!(
            c,
            ',' | ';' | '(' | ')' | '「' | '」' | '『' | '』' | '。' | '、'
        )
}

/// `(?:~|/)[^\s,;()「」『』,。、]+` matches, left to right.
fn paths_in(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < text.len() {
        let Some(c) = text[i..].chars().next() else {
            break;
        };
        if c == '~' || c == '/' {
            let body: usize = text[i + 1..]
                .chars()
                .take_while(|c| !path_stop(*c))
                .map(char::len_utf8)
                .sum();
            if body > 0 {
                out.push(&text[i..i + 1 + body]);
                i += 1 + body;
                continue;
            }
        }
        i += c.len_utf8();
    }
    out
}

/// The whole string is exactly one piece of evidence (evidence_kind).
pub fn kind_of(seat: &str, evidence: &str, probe: &dyn Probe) -> Option<Kind> {
    let ev = py_strip(evidence);
    if ev.is_empty() {
        return None;
    }
    if is_item_id(ev) {
        return probe.reply_sent_by(seat, ev).then_some(Kind::Dm);
    }
    if is_pr_url(ev) {
        return Some(Kind::Pr);
    }
    if is_sha(ev) && probe.commit_exists(ev) {
        return Some(Kind::Commit);
    }
    probe.is_file(ev).then_some(Kind::File)
}

/// (kind, the part that was checked) for `--evidence`, or (None, None) (evidence_ref).
pub fn classify(
    seat: &str,
    evidence: Option<&str>,
    probe: &dyn Probe,
) -> (Option<Kind>, Option<String>) {
    let ev = evidence.map(py_strip).unwrap_or("");
    let kind = kind_of(seat, ev, probe);
    if kind.is_some() || ev.is_empty() {
        return (kind, kind.map(|_| ev.to_string()));
    }
    if let Some(u) = pr_url_in(ev) {
        return (Some(Kind::Pr), Some(u.to_string()));
    }
    let candidates = item_ids_in(ev)
        .into_iter()
        .chain(shas_in(ev))
        .chain(paths_in(ev));
    for (n, tok) in candidates.enumerate() {
        if n >= SCAN_MAX {
            return (None, None);
        }
        let tok = tok.trim_end_matches(['.', ':']);
        if let Some(k) = kind_of(seat, tok, probe) {
            return (Some(k), Some(tok.to_string()));
        }
    }
    (None, None)
}
