//! Stale-head gate (seat continuity MVP-1, DECISION-seat-continuity v2 §1 #1;
//! Andy 11-12 00:39 「照 v2 做，讓 1ced7 先寫 MVP-1」).
//!
//! Before a dm or a review's first line goes out, every 8–40 character
//! lowercase hex run in it is looked up on GitHub, GET-only, through
//! aib-drive's [`Read`] (the reader the drive snapshot uses). A run written
//! where a head goes (after `頭` / `頭:` / `現頭` / `頭碼` / `head` / `推了` /
//! `推到` / `pushed`, or right of `..`) that is a commit of an open PR but not
//! its current head is `stale_head`: the text is held back and the current
//! head of every open PR the commit belongs to is printed. On 11-11 a head
//! typed wrong went to two seats; nothing that only checked the format could
//! see it.
//!
//! What is *not* held back:
//! - a prefix of an open PR's current head;
//! - an old commit named anywhere else ("修在 7dea3809", `舊頭 X`, the base of
//!   `<old>..<new>`): a reminder (a9136 0084435f L1: blocking every mention
//!   teaches everyone to send with the override);
//! - an old commit of a PR whose current head the same text also names
//!   ("was X, now Y"). Stacked PRs share commits (GitHub ties a lower PR's
//!   commits to the upper one too), so naming the current head of *any* open
//!   PR the commit belongs to is enough (a9136 0084435f M1);
//! - a commit GitHub does not know (not pushed yet, or mistyped): a reminder,
//!   never a block — a local head that is about to be pushed is normal;
//! - a commit in no open PR (on main, on a branch with no PR, or dropped by a
//!   force push: GitHub no longer ties those to the PR, a known gap).
//!
//! What can't be read is never treated as "fine" or as "stale": it is
//! `unverified` with a reason, printed, and does not block. With fewer than
//! [`QUOTA_FLOOR`] GitHub calls left (or the quota unreadable) the gate makes
//! no lookups at all and only reminds.
//!
//! Not checked: 7-character short heads (`git rev-parse --short`, GitHub's
//! web view) and upper case. This repo's `git log --oneline` prints 8.

use aib_drive::snapshot::Read;
use serde_json::{json, Value};
use std::collections::BTreeMap;

/// Below this many GitHub calls left the gate only reminds (the observe
/// loop's floor, PR-C).
pub const QUOTA_FLOOR: u64 = 1500;

/// Most commit lookups for one text; the rest are `unverified`.
pub const MAX_LOOKUPS: usize = 10;

/// Largest page GitHub gives; a list this long may have more.
const PER_PAGE: usize = 100;

/// One hex run found in the text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Token {
    pub hex: String,
    /// Written at least once where a head goes (see the crate doc).
    pub as_head: bool,
}

/// Words after which a hex run is claimed to be a head (`已推 X`, `推上 X`,
/// `現在頭 X` too: a9136 f3d08c94 L1). `舊頭` / `原頭` / `前頭` name an old
/// one on purpose.
const HEAD_WORDS: &[&str] = &["頭", "頭碼", "head", "推", "推了", "推到", "推上", "pushed"];
const OLD_WORDS: &[&str] = &["舊頭", "原頭", "前頭", "舊頭碼", "原頭碼", "old head"];

/// Is the run starting at `start` written where a head goes?
fn head_position(cs: &[char], start: usize) -> bool {
    // right of `..`
    if start >= 2 && cs[start - 1] == '.' && cs[start - 2] == '.' {
        return true;
    }
    let mut k = start;
    while k > 0 && matches!(cs[k - 1], ' ' | ':' | '\u{ff1a}' | '=' | '\u{3000}') {
        k -= 1;
    }
    let before: String = cs[k.saturating_sub(8)..k]
        .iter()
        .collect::<String>()
        .to_lowercase();
    HEAD_WORDS.iter().any(|w| ends(&before, w)) && !OLD_WORDS.iter().any(|w| ends(&before, w))
}

/// `before` ends with the word `w`; an English word must not be the tail of
/// a longer one (`ahead`, `overhead`: a9136 f3d08c94 N1).
fn ends(before: &str, w: &str) -> bool {
    let Some(rest) = before.strip_suffix(w) else {
        return false;
    };
    !(w.starts_with(|c: char| c.is_ascii_alphabetic())
        && rest.ends_with(|c: char| c.is_ascii_alphabetic()))
}

fn is_word(c: char) -> bool {
    c.is_ascii_alphanumeric() || c == '_'
}

/// The 8–40 character lowercase hex runs of `text` that stand alone (not
/// part of a longer word such as `q_812cfc2d5b53cc42` or a URL path piece
/// glued to letters), with at least one letter a–f (a run of digits is a
/// number or a date, not a head). Each run once, in order of first use.
pub fn tokens(text: &str) -> Vec<Token> {
    let cs: Vec<char> = text.chars().collect();
    let mut out: Vec<Token> = Vec::new();
    let mut i = 0;
    while i < cs.len() {
        if !is_word(cs[i]) {
            i += 1;
            continue;
        }
        let start = i;
        while i < cs.len() && is_word(cs[i]) {
            i += 1;
        }
        let word: String = cs[start..i].iter().collect();
        let hex = word
            .chars()
            .all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c));
        let has_letter = word.chars().any(|c| ('a'..='f').contains(&c));
        if !(hex && has_letter && (8..=40).contains(&word.len())) {
            continue;
        }
        let as_head = head_position(&cs, start);
        match out.iter_mut().find(|t| t.hex == word) {
            // written once as a head is enough to hold it to that
            Some(t) => t.as_head |= as_head,
            None => out.push(Token { hex: word, as_head }),
        }
    }
    out
}

/// What one token turned out to be.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// A prefix of this open PR's current head.
    Current { pr: u64 },
    /// Written as a head, but a commit of these open PRs that is not their
    /// head; each with its current head. Held back.
    StaleHead { heads: Vec<(u64, String)> },
    /// An old commit, and the text names the current head of one of its
    /// PRs too: shown, not held back.
    StaleNamed { heads: Vec<(u64, String)> },
    /// An old commit not written as a head: shown, not held back.
    StaleMention { heads: Vec<(u64, String)> },
    /// GitHub has no such commit (not pushed, or mistyped).
    NotOnGithub,
    /// A known commit in no open PR.
    NoOpenPr,
    /// Could not be checked (`reason`); never fine, never stale.
    Unverified { reason: String },
}

impl Verdict {
    pub fn code(&self) -> &'static str {
        match self {
            Self::Current { .. } => "current",
            Self::StaleHead { .. } => "stale_head",
            Self::StaleNamed { .. } => "stale_named",
            Self::StaleMention { .. } => "stale_mention",
            Self::NotOnGithub => "not_on_github",
            Self::NoOpenPr => "no_open_pr",
            Self::Unverified { .. } => "unverified",
        }
    }
}

/// The gate's answer for one text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Report {
    /// `None` = the quota could not be read.
    pub quota: Option<u64>,
    /// false = only reminds (low or unknown quota).
    pub enforce: bool,
    pub findings: Vec<(Token, Verdict)>,
}

impl Report {
    /// Held back: at least one `stale_head` while enforcing.
    pub fn blocked(&self) -> bool {
        self.enforce
            && self
                .findings
                .iter()
                .any(|(_, v)| matches!(v, Verdict::StaleHead { .. }))
    }

    pub fn to_json(&self) -> Value {
        let findings: Vec<Value> = self
            .findings
            .iter()
            .map(|(t, v)| {
                let mut o = json!({"token": t.hex, "verdict": v.code()});
                match v {
                    Verdict::Current { pr } => o["pr"] = json!(pr),
                    Verdict::StaleHead { heads }
                    | Verdict::StaleNamed { heads }
                    | Verdict::StaleMention { heads } => {
                        o["heads"] = heads
                            .iter()
                            .map(|(pr, head)| json!({"pr": pr, "head": head}))
                            .collect();
                    }
                    Verdict::Unverified { reason } => o["reason"] = json!(reason),
                    Verdict::NotOnGithub | Verdict::NoOpenPr => {}
                }
                o
            })
            .collect();
        json!({
            "blocked": self.blocked(),
            "reason": if self.blocked() { Some("stale_head") } else { None },
            "enforce": self.enforce,
            "quota": self.quota,
            "findings": findings,
        })
    }

    /// One line per finding a person should see (current heads and commits
    /// in no open PR say nothing).
    pub fn lines(&self) -> Vec<String> {
        let mut out = Vec::new();
        if !self.enforce {
            out.push(match self.quota {
                Some(q) => {
                    format!("stale-head: GitHub 配額剩 {q} < {QUOTA_FLOOR},頭碼未核(只提醒、不擋)")
                }
                None => "stale-head: 讀不到 GitHub 配額,頭碼未核(只提醒、不擋)".to_owned(),
            });
        }
        for (t, v) in &self.findings {
            let h = &t.hex;
            match v {
                Verdict::StaleHead { heads } => out.push(format!(
                    "stale_head: {h} 寫在頭的位置,但它是 {} 的舊 commit;改成現頭再送(也查 dm 點名的審查稿第一行;確定要寫 / 轉交舊的:AIB_DM_ACK_STALE=1)",
                    list(heads)
                )),
                Verdict::StaleNamed { heads } => out.push(format!(
                    "stale-head 提醒: {h} 是 {} 的舊 commit(同一段文字已寫到現頭,不擋)",
                    list(heads)
                )),
                Verdict::StaleMention { heads } => out.push(format!(
                    "stale-head 提醒: {h} 是 {} 的舊 commit(不在頭的位置,不擋)",
                    list(heads)
                )),
                Verdict::NotOnGithub => out.push(format!(
                    "stale-head 提醒: {h} 在 GitHub 上找不到(還沒推,或打錯)"
                )),
                Verdict::Unverified { reason } => {
                    out.push(format!("stale-head 提醒: {h} 未核({reason})"));
                }
                Verdict::Current { .. } | Verdict::NoOpenPr => {}
            }
        }
        out
    }
}

/// `#684(現頭 728c663a)、#685(現頭 e8e61c1c)`.
fn list(heads: &[(u64, String)]) -> String {
    heads
        .iter()
        .map(|(pr, h)| format!("#{pr}(現頭 {})", &h[..h.len().min(8)]))
        .collect::<Vec<_>>()
        .join("、")
}

/// GitHub calls left this hour (`gh api rate_limit`); `None` = unreadable.
pub trait Quota {
    fn remaining(&self) -> Option<u64>;
}

/// [`Quota`] through `gh api rate_limit` (GET; the token stays with `gh`).
pub struct GhQuota {
    pub program: String,
    pub timeout: std::time::Duration,
}

impl Quota for GhQuota {
    fn remaining(&self) -> Option<u64> {
        let args = ["api".to_owned(), "rate_limit".to_owned()];
        let out = aib_drive::source::run_fixed(&self.program, &args, self.timeout).ok()?;
        let v: Value = serde_json::from_slice(&out).ok()?;
        v.pointer("/resources/core/remaining")
            .and_then(Value::as_u64)
    }
}

fn is_sha40(s: &str) -> bool {
    aib_core::firstline::is_sha40(s)
}

/// Open PR number → current head, read in full or not at all.
fn open_heads(gh: &dyn Read, repo: &str) -> Result<BTreeMap<u64, String>, String> {
    let v = gh.get(&format!(
        "repos/{repo}/pulls?state=open&per_page={PER_PAGE}"
    ))?;
    let list = v.as_array().ok_or("open pulls: not a list")?;
    if list.len() >= PER_PAGE {
        return Err(format!(
            "open pulls: {} items, may have a next page",
            list.len()
        ));
    }
    let mut out = BTreeMap::new();
    for p in list {
        let n = p.get("number").and_then(Value::as_u64);
        let h = p.pointer("/head/sha").and_then(Value::as_str);
        match (n, h) {
            (Some(n), Some(h)) if is_sha40(h) => {
                out.insert(n, h.to_owned());
            }
            _ => return Err("open pulls: an entry without number / head".to_owned()),
        }
    }
    Ok(out)
}

/// The open PRs GitHub ties `hex` to (`repos/…/commits/<hex>/pulls`).
/// `Ok(None)` = GitHub has no such commit.
fn pulls_of(gh: &dyn Read, repo: &str, hex: &str) -> Result<Option<Vec<u64>>, String> {
    match gh.get(&format!("repos/{repo}/commits/{hex}/pulls")) {
        Ok(v) => {
            let list = v.as_array().ok_or("commit pulls: not a list")?;
            let mut out = Vec::new();
            for p in list {
                let open = p.get("state").and_then(Value::as_str) == Some("open");
                let n = p.get("number").and_then(Value::as_u64);
                match n {
                    Some(n) if open => out.push(n),
                    Some(_) => {}
                    None => return Err("commit pulls: an entry without number".to_owned()),
                }
            }
            Ok(Some(out))
        }
        // gh: "No commit found for SHA: … (HTTP 422)"
        Err(e) if e.contains("No commit found") => Ok(None),
        Err(e) => Err(e),
    }
}

/// The gate. `text` is the whole dm (or a review's first line).
pub fn check(gh: &dyn Read, quota: &dyn Quota, repo: &str, text: &str) -> Report {
    let toks = tokens(text);
    if toks.is_empty() {
        // nothing to look up: no call at all, not even the quota
        return Report {
            quota: None,
            enforce: true,
            findings: Vec::new(),
        };
    }
    let q = quota.remaining();
    let enforce = q.is_some_and(|q| q >= QUOTA_FLOOR);
    if !enforce {
        let findings = toks
            .into_iter()
            .map(|t| {
                (
                    t,
                    Verdict::Unverified {
                        reason: "quota_low".to_owned(),
                    },
                )
            })
            .collect();
        return Report {
            quota: q,
            enforce,
            findings,
        };
    }
    let heads = match open_heads(gh, repo) {
        Ok(h) => h,
        Err(e) => {
            let findings = toks
                .into_iter()
                .map(|t| {
                    (
                        t,
                        Verdict::Unverified {
                            reason: format!("open_pulls: {e}"),
                        },
                    )
                })
                .collect();
            return Report {
                quota: q,
                enforce,
                findings,
            };
        }
    };
    let current_of = |hex: &str| {
        heads
            .iter()
            .find(|(_, h)| h.starts_with(hex))
            .map(|(n, _)| *n)
    };
    // PRs whose current head the text names
    let named: Vec<u64> = toks.iter().filter_map(|t| current_of(&t.hex)).collect();
    let mut lookups = 0;
    let mut findings = Vec::new();
    for t in toks {
        if let Some(pr) = current_of(&t.hex) {
            findings.push((t, Verdict::Current { pr }));
            continue;
        }
        if lookups == MAX_LOOKUPS {
            findings.push((
                t,
                Verdict::Unverified {
                    reason: format!("more_than_{MAX_LOOKUPS}_lookups"),
                },
            ));
            continue;
        }
        lookups += 1;
        let v = match pulls_of(gh, repo, &t.hex) {
            Err(e) => Verdict::Unverified {
                reason: format!("commit_pulls: {e}"),
            },
            Ok(None) => Verdict::NotOnGithub,
            Ok(Some(prs)) => {
                // every open PR the commit belongs to (stacked PRs share commits)
                let open: Vec<(u64, String)> = prs
                    .iter()
                    .filter_map(|n| heads.get(n).map(|h| (*n, h.clone())))
                    .collect();
                if open.is_empty() {
                    Verdict::NoOpenPr
                } else if open.iter().any(|(n, _)| named.contains(n)) {
                    Verdict::StaleNamed { heads: open }
                } else if !t.as_head {
                    Verdict::StaleMention { heads: open }
                } else {
                    Verdict::StaleHead { heads: open }
                }
            }
        };
        findings.push((t, v));
    }
    Report {
        quota: q,
        enforce,
        findings,
    }
}

/// The text to check for a review file: its first line only (the label line
/// carries `頭:` and the range; the body may discuss old commits freely).
pub fn review_first_line(file_text: &str) -> &str {
    let t = file_text.strip_prefix('\u{feff}').unwrap_or(file_text);
    t.lines().next().unwrap_or("").trim_end_matches('\r')
}

/// `REVIEW-*.md` / `VERIFY-*.md` paths a dm names (absolute or `~/`), in
/// order, once each. A dm that hands over a review sends its first line too.
pub fn review_paths(text: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for w in text.split(|c: char| {
        c.is_whitespace()
            || "(),;\u{ff08}\u{ff09}\u{ff0c}\u{ff1b}\u{ff1a}\u{300c}\u{300d}\u{3001}\u{3002}"
                .contains(c)
    }) {
        let w = w.trim_end_matches(['.', ':', '\u{3002}', '\u{ff1a}']);
        // `審完:/path` (ASCII colon glued to a word; not split on, `C:\` is a path)
        let w = w
            .find(":/")
            .or_else(|| w.find(":~/"))
            .map_or(w, |i| &w[i + 1..]);
        let name = w.rsplit(['/', '\\']).next().unwrap_or("");
        let is_note =
            (name.starts_with("REVIEW-") || name.starts_with("VERIFY-")) && name.ends_with(".md");
        let is_path = w.starts_with('/') || w.starts_with("~/") || w.contains(":\\");
        if is_note && is_path && !out.iter().any(|o| o == w) {
            out.push(w.to_owned());
        }
    }
    out
}
