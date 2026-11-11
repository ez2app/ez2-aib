//! The fixed first line of a review (`REVIEW-*.md`) and of an acceptance run
//! (`VERIFY-*.md`), as RULES-seats-aib2 §1 and `aib-verify` write them:
//!
//! ```text
//! 結論: 通過|不通過 頭: <40 hex> H/M/L/N: a/b/c/d 範圍: <全文|增量 <40>..<40>>…
//! 驗收: 通過|不通過 頭: <40 hex> 機: <host> 樣本: <…> 沒驗: <…|無>
//! ```
//!
//! The merge chain (`aib-mergeq integ collect`, `aib-drive`) reads only this
//! line; the rest of the file is data, not instructions. Parsing is strict on
//! what a merge decides on (verdict, head, counts) and lenient on free text:
//! a 範圍 that is not `全文` / `增量 <40>..<40>` parses as [`Scope::Other`],
//! and [`Review::conforms`] says whether the line follows §1 in full.

use core::fmt;

/// `通過` / `不通過`. Nothing else is a verdict (`預審 不通過`, `通過(附 …)`,
/// `不過` are refused: a merge must not guess).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    Pass,
    Fail,
}

impl Verdict {
    fn parse(s: &str) -> Option<Self> {
        match s {
            "通過" => Some(Self::Pass),
            "不通過" => Some(Self::Fail),
            _ => None,
        }
    }
}

/// What a review covered.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Scope<'a> {
    /// `全文` (any text after it is a note).
    Full,
    /// `增量 <from 40>..<to 40>` (any text after it is a note).
    Incremental { from: &'a str, to: &'a str },
    /// Anything else (short heads, `全分支 …`, `合併 commit …`): readable, but
    /// not §1. The merge chain treats it as not conforming.
    Other(&'a str),
}

/// H/M/L/N counts.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Counts {
    pub high: u32,
    pub medium: u32,
    pub low: u32,
    pub nit: u32,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Review<'a> {
    pub verdict: Verdict,
    pub head: &'a str,
    pub counts: Counts,
    pub scope: Scope<'a>,
}

impl Review<'_> {
    /// The line follows §1 in full: a known scope, and an incremental range
    /// ends at the head it reviews.
    pub fn conforms(&self) -> bool {
        match self.scope {
            Scope::Full => true,
            Scope::Incremental { to, .. } => to == self.head,
            Scope::Other(_) => false,
        }
    }

    /// A pass with no high or medium finding left (a `通過` with M > 0 is a
    /// contradiction the merge chain must not resolve on its own). This looks
    /// at the review alone: a merge must use [`Review::mergeable_at`] (one `全文` review) or
    /// [`chain_mergeable_at`] (a chain of increments).
    pub fn mergeable(&self) -> bool {
        self.conforms()
            && self.verdict == Verdict::Pass
            && self.counts.high == 0
            && self.counts.medium == 0
    }

    /// This one review alone lets `head` (the PR's head now) merge: it is
    /// [`Review::mergeable`], for `head` (a review of an older head never
    /// merges a newer one, a9136 c3d2f112 L1), and covers the whole PR
    /// (`全文`). An incremental review covers only its range, so it never
    /// passes here on its own (a63ab 11-10: an old head reviewed in full and
    /// refused, then a new head passed on the increment alone, must not merge
    /// on that one file): use [`chain_mergeable_at`] for a chain.
    pub fn mergeable_at(&self, head: &str) -> bool {
        self.head == head && self.scope == Scope::Full && self.mergeable()
    }
}

/// One review and who wrote it. `by` is the seat the caller read the review
/// from (the file name today, so self-claimed; a9136 decide checks it against
/// the commit authors). An empty `by` never joins a fix cycle.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Signed<'a> {
    pub by: &'a str,
    pub review: Review<'a>,
}

/// The reviews of one PR let `head` merge:
///
/// - no conforming review **of `head`** refuses it (`不通過`, or H / M left):
///   one reviewer's refusal is not outvoted by another's pass;
/// - one review of `head` is [`Review::mergeable`];
/// - its coverage reaches a `全文` review: an incremental review's `from` must
///   be the head of another conforming review, and so on back to a `全文` one.
///
/// A link that refused its head (not [`Review::mergeable`]) is the fix cycle
/// (refused, fixed, the fix passed on the increment; #589 95ade73f ⇒
/// 2af03401). RULES §1 (a63ab 11-10 02:0x): it joins only the increment
/// written by **the same reviewer**; another reviewer's increment on a refusal
/// breaks the chain. That the increment closes each old H / M item is a rule
/// of the review text, not judged here. A range nobody reviewed breaks the
/// chain; non-conforming reviews (a short range, `全分支 …`) never count.
///
/// Left to the caller: that `from` is an ancestor of `head` with no commit
/// skipped (this only compares heads as strings, so a force-pushed old head
/// still links; a9136 07185ccf L1), and that `by` is who it claims to be.
pub fn chain_mergeable_at(reviews: &[Signed<'_>], head: &str) -> bool {
    let at_head = || {
        reviews
            .iter()
            .filter(|s| s.review.head == head && s.review.conforms())
    };
    if at_head().any(|s| !s.review.mergeable()) {
        return false;
    }
    at_head().any(|s| covers_from_full(reviews, s, reviews.len()))
}

/// `s`'s coverage reaches a `全文` review in at most `left` links (the bound
/// stops a cycle of ranges).
fn covers_from_full(reviews: &[Signed<'_>], s: &Signed<'_>, left: usize) -> bool {
    match s.review.scope {
        Scope::Full => true,
        Scope::Incremental { from, .. } => {
            left > 0
                && reviews
                    .iter()
                    .filter(|p| p.review.head == from && p.review.conforms())
                    .filter(|p| p.review.mergeable() || joins_fix_cycle(p, s))
                    .any(|p| covers_from_full(reviews, p, left - 1))
        }
        Scope::Other(_) => false,
    }
}

/// The increment `inc` picks up the refused review `refused`: same reviewer.
fn joins_fix_cycle(refused: &Signed<'_>, inc: &Signed<'_>) -> bool {
    !refused.by.is_empty() && refused.by == inc.by
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Verify<'a> {
    pub verdict: Verdict,
    pub head: &'a str,
    pub host: &'a str,
    pub samples: &'a str,
    /// `None` for `無`; otherwise the list as written. A skipped check is never
    /// a pass: the merge chain must find each item in the known-issues notes or
    /// a DECISION file.
    pub not_checked: Option<&'a str>,
}

/// Why a first line was refused.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FirstLineError {
    /// The line does not start with the label (`結論: ` / `驗收: `).
    Label,
    /// A required field is missing or out of order.
    Field(&'static str),
    Verdict,
    /// The head is not 40 lowercase hex.
    Head,
    Counts,
}

impl fmt::Display for FirstLineError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Label => f.write_str("first line label missing"),
            Self::Field(name) => write!(f, "field {name} missing or out of order"),
            Self::Verdict => f.write_str("verdict is not 通過 / 不通過"),
            Self::Head => f.write_str("head is not 40 lowercase hex"),
            Self::Counts => f.write_str("H/M/L/N is not four numbers a/b/c/d"),
        }
    }
}

/// 40 lowercase hex (a full git SHA-1).
pub fn is_sha40(s: &str) -> bool {
    s.len() == 40
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Split `rest` at the next ` <key> ` marker: (`before`, `after`).
fn take<'a>(rest: &'a str, key: &'static str) -> Result<(&'a str, &'a str), FirstLineError> {
    let marker_at = rest.find(key).ok_or(FirstLineError::Field(key))?;
    let before = rest[..marker_at].trim_end_matches(' ');
    let after = &rest[marker_at + key.len()..];
    if marker_at == 0 || !rest[..marker_at].ends_with(' ') {
        return Err(FirstLineError::Field(key));
    }
    Ok((before, after))
}

fn head(s: &str) -> Result<&str, FirstLineError> {
    if is_sha40(s) {
        Ok(s)
    } else {
        Err(FirstLineError::Head)
    }
}

fn counts(s: &str) -> Result<Counts, FirstLineError> {
    let mut it = s.split('/').map(|n| {
        if n.is_empty() || !n.bytes().all(|b| b.is_ascii_digit()) {
            return Err(FirstLineError::Counts);
        }
        n.parse::<u32>().map_err(|_| FirstLineError::Counts)
    });
    let mut next = || it.next().unwrap_or(Err(FirstLineError::Counts));
    let c = Counts {
        high: next()?,
        medium: next()?,
        low: next()?,
        nit: next()?,
    };
    if it.next().is_some() {
        return Err(FirstLineError::Counts);
    }
    Ok(c)
}

fn scope(s: &str) -> Scope<'_> {
    let s = s.trim();
    if s == "全文" || s.starts_with("全文 ") || s.starts_with("全文(") {
        return Scope::Full;
    }
    if let Some(range) = s.strip_prefix("增量 ") {
        let range = range.split([' ', '(', '(']).next().unwrap_or("");
        if let Some((from, to)) = range.split_once("..") {
            if is_sha40(from) && is_sha40(to) {
                return Scope::Incremental { from, to };
            }
        }
    }
    Scope::Other(s)
}

/// Only the first line of `text` (a whole file may be passed). A leading
/// byte order mark, which some editors write, is not part of the label
/// (a9136 c3d2f112 L2).
fn first(text: &str) -> &str {
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    text.lines().next().unwrap_or("").trim_end_matches('\r')
}

/// Parse a review's first line (or a whole review file: only its first line is read).
pub fn parse_review(text: &str) -> Result<Review<'_>, FirstLineError> {
    let line = first(text);
    let rest = line.strip_prefix("結論: ").ok_or(FirstLineError::Label)?;
    let (verdict, rest) = take(rest, "頭: ")?;
    let verdict = Verdict::parse(verdict).ok_or(FirstLineError::Verdict)?;
    let (h, rest) = take(rest, "H/M/L/N: ")?;
    let head = head(h)?;
    let (c, rest) = take(rest, "範圍: ")?;
    let counts = counts(c)?;
    if rest.trim().is_empty() {
        return Err(FirstLineError::Field("範圍: "));
    }
    Ok(Review {
        verdict,
        head,
        counts,
        scope: scope(rest),
    })
}

/// Parse an acceptance run's first line (or a whole `VERIFY-*.md`).
pub fn parse_verify(text: &str) -> Result<Verify<'_>, FirstLineError> {
    let line = first(text);
    let rest = line.strip_prefix("驗收: ").ok_or(FirstLineError::Label)?;
    let (verdict, rest) = take(rest, "頭: ")?;
    let verdict = Verdict::parse(verdict).ok_or(FirstLineError::Verdict)?;
    let (h, rest) = take(rest, "機: ")?;
    let head = head(h)?;
    let (host, rest) = take(rest, "樣本: ")?;
    if host.is_empty() || host.contains(' ') {
        return Err(FirstLineError::Field("機: "));
    }
    // 沒驗 is last and its list runs to the end of the line: split at the first
    // marker, so a list that itself mentions ` 沒驗: ` is kept whole (a shorter
    // list would hide a skipped check; a9136 c3d2f112 N2)
    let at = rest
        .find(" 沒驗: ")
        .ok_or(FirstLineError::Field("沒驗: "))?;
    let samples = rest[..at].trim();
    let not_checked = rest[at + " 沒驗: ".len()..].trim();
    if samples.is_empty() {
        return Err(FirstLineError::Field("樣本: "));
    }
    if not_checked.is_empty() {
        return Err(FirstLineError::Field("沒驗: "));
    }
    Ok(Verify {
        verdict,
        head,
        host,
        samples,
        not_checked: (not_checked != "無").then_some(not_checked),
    })
}

#[cfg(test)]
#[allow(clippy::unwrap_used)] // test code: a panic is the failure report
mod tests {
    use super::*;

    const A: &str = "967111ff447dcd6d6c561df2f860d95d698109d0";
    const B: &str = "cd33802bf498b4c66d853097bbef5ffd85832eef";

    fn review(rest: &str) -> std::string::String {
        std::format!("結論: 通過 頭: {B} H/M/L/N: 0/0/1/2 範圍: {rest}")
    }

    #[test]
    fn a_review_line_by_the_rule_parses() {
        let line = std::format!("結論: 不通過 頭: {B} H/M/L/N: 0/1/1/2 範圍: 增量 {A}..{B}");
        let r = parse_review(&line).unwrap();
        assert_eq!(r.verdict, Verdict::Fail);
        assert_eq!(r.head, B);
        assert_eq!(
            r.counts,
            Counts {
                high: 0,
                medium: 1,
                low: 1,
                nit: 2
            }
        );
        assert_eq!(r.scope, Scope::Incremental { from: A, to: B });
        assert!(r.conforms());
        assert!(!r.mergeable());
    }

    #[test]
    fn only_the_first_line_is_read() {
        let file = std::format!(
            "{}\n結論: 不通過 頭: {A} H/M/L/N: 9/9/9/9 範圍: 全文\n",
            review("全文")
        );
        let r = parse_review(&file).unwrap();
        assert_eq!(r.verdict, Verdict::Pass);
        assert_eq!(r.head, B);
        assert!(r.mergeable());
    }

    #[test]
    fn a_note_after_the_scope_is_kept_out_of_the_range() {
        let line = review(&std::format!("增量 {A}..{B} 的 M1 / M2 修補"));
        let r = parse_review(&line).unwrap();
        assert_eq!(r.scope, Scope::Incremental { from: A, to: B });
        let line = review(&std::format!("增量 {A}..{B}(1 commit)"));
        let r = parse_review(&line).unwrap();
        assert!(r.conforms());
        assert_eq!(
            parse_review(&review("全文 origin/main..cc019fe0"))
                .unwrap()
                .scope,
            Scope::Full
        );
    }

    #[test]
    fn a_scope_outside_the_rule_is_readable_but_does_not_conform() {
        for s in [
            "增量 967111ff..cd33802b",
            "全分支 origin/main...49eaca27",
            "合併 commit 75e6dab0 的解衝突段",
        ] {
            let line = review(s);
            let r = parse_review(&line).unwrap();
            assert!(matches!(r.scope, Scope::Other(_)), "{s}");
            assert!(!r.conforms() && !r.mergeable(), "{s}");
        }
        // a range that ends at another head does not cover this one
        let line = review(&std::format!("增量 {B}..{A}"));
        let r = parse_review(&line).unwrap();
        assert!(!r.conforms());
    }

    #[test]
    fn a_pass_with_a_medium_left_is_not_mergeable() {
        let line = std::format!("結論: 通過 頭: {B} H/M/L/N: 0/2/0/0 範圍: 全文");
        let r = parse_review(&line).unwrap();
        assert!(r.conforms());
        assert!(!r.mergeable());
    }

    #[test]
    fn anything_but_the_two_verdicts_is_refused() {
        for v in ["預審 不通過", "通過(附 2 Medium 要改)", "不過", "pass", ""] {
            let line = std::format!("結論: {v} 頭: {B} H/M/L/N: 0/0/0/0 範圍: 全文");
            assert!(parse_review(&line).is_err(), "{v}");
        }
    }

    #[test]
    fn the_head_is_a_full_lowercase_sha() {
        for h in [
            "cd33802b",
            "CD33802BF498B4C66D853097BBEF5FFD85832EEF",
            "設計稿 DESIGN-x.md",
            "can-push-macos.sh sha256 e6964c86",
        ] {
            let line = std::format!("結論: 通過 頭: {h} H/M/L/N: 0/0/0/0 範圍: 全文");
            assert_eq!(
                parse_review(&line).unwrap_err(),
                FirstLineError::Head,
                "{h}"
            );
        }
    }

    #[test]
    fn counts_are_four_plain_numbers() {
        for c in [
            "0/0/0",
            "0/0/0/0/0",
            "0/-1/0/0",
            "0/a/0/0",
            "0//0/0",
            "0/+1/0/0",
        ] {
            let line = std::format!("結論: 通過 頭: {B} H/M/L/N: {c} 範圍: 全文");
            assert_eq!(
                parse_review(&line).unwrap_err(),
                FirstLineError::Counts,
                "{c}"
            );
        }
    }

    #[test]
    fn fields_must_be_there_and_in_order() {
        assert_eq!(
            parse_review("# 審查稿: ez2-aib #486").unwrap_err(),
            FirstLineError::Label
        );
        assert_eq!(
            parse_review("結論:通過 頭: x").unwrap_err(),
            FirstLineError::Label
        );
        let no_scope = std::format!("結論: 通過 頭: {B} H/M/L/N: 0/0/0/0");
        assert_eq!(
            parse_review(&no_scope).unwrap_err(),
            FirstLineError::Field("範圍: ")
        );
        let empty_scope = std::format!("結論: 通過 頭: {B} H/M/L/N: 0/0/0/0 範圍: ");
        assert_eq!(
            parse_review(&empty_scope).unwrap_err(),
            FirstLineError::Field("範圍: ")
        );
        let swapped = std::format!("結論: 通過 H/M/L/N: 0/0/0/0 頭: {B} 範圍: 全文");
        assert!(parse_review(&swapped).is_err());
    }

    #[test]
    fn a_merge_uses_the_head_the_review_was_for() {
        let line = review("全文");
        let r = parse_review(&line).unwrap();
        assert!(r.mergeable());
        assert!(r.mergeable_at(B));
        assert!(
            !r.mergeable_at(A),
            "a review of an older head merged a newer one"
        );
    }

    const C: &str = "2af03401a124ce7276eb2f180cdf809a346a5d2d";

    fn line(verdict: &str, head: &str, counts: &str, scope: &str) -> std::string::String {
        std::format!("結論: {verdict} 頭: {head} H/M/L/N: {counts} 範圍: {scope}")
    }

    fn by<'a>(who: &'a str, l: &'a str) -> Signed<'a> {
        Signed {
            by: who,
            review: parse_review(l).unwrap(),
        }
    }

    /// a63ab 11-10 (decide had the same hole): the old head refused in full,
    /// the new head passed on the increment. That increment alone is not a
    /// review of the PR.
    #[test]
    fn an_increment_alone_never_merges_on_its_own() {
        let full_no = line("不通過", A, "0/2/0/0", "全文");
        let inc_ok = line("通過", B, "0/0/0/0", &std::format!("增量 {A}..{B}"));
        let inc = by("bb7dc", &inc_ok);
        assert!(inc.review.mergeable(), "the increment itself is clean");
        assert!(
            !inc.review.mergeable_at(B),
            "an incremental review merged a PR on its own"
        );
        // nobody reviewed base..A: the chain is broken
        assert!(!chain_mergeable_at(&[inc], B));
        // the same reviewer refused A in full: the fix cycle (#589), the chain holds
        let full = by("bb7dc", &full_no);
        assert!(!full.review.mergeable_at(A));
        assert!(chain_mergeable_at(&[full, inc], B));
        // and still nothing merges at the old head
        assert!(!chain_mergeable_at(&[full, inc], A));
    }

    /// RULES §1 (a63ab 11-10 02:0x, a9136 07185ccf M1): a refusal is picked
    /// up only by the same reviewer's increment.
    #[test]
    fn another_reviewer_cannot_close_a_refusal() {
        let full_no = line("不通過", A, "0/2/0/0", "全文");
        let inc_ok = line("通過", B, "0/0/0/0", &std::format!("增量 {A}..{B}"));
        assert!(!chain_mergeable_at(
            &[by("043dd", &full_no), by("bb7dc", &inc_ok)],
            B
        ));
        // an empty name is nobody, even twice
        assert!(!chain_mergeable_at(&[by("", &full_no), by("", &inc_ok)], B));
        // a passing link joins anyone's increment
        let full_ok = line("通過", A, "0/0/1/0", "全文");
        assert!(chain_mergeable_at(
            &[by("043dd", &full_ok), by("bb7dc", &inc_ok)],
            B
        ));
        // a refusal and a pass of the same old head: the pass still carries
        assert!(chain_mergeable_at(
            &[
                by("043dd", &full_no),
                by("a9136", &full_ok),
                by("bb7dc", &inc_ok)
            ],
            B
        ));
    }

    #[test]
    fn a_refusal_of_the_head_is_not_outvoted() {
        let yes = line("通過", B, "0/0/0/0", "全文");
        let no = line("不通過", B, "0/1/0/0", "全文");
        let m_left = line("通過", B, "0/1/0/0", "全文");
        let (yes, no, m_left) = (by("x", &yes), by("y", &no), by("z", &m_left));
        assert!(chain_mergeable_at(&[yes], B));
        assert!(!chain_mergeable_at(&[yes, no], B));
        assert!(!chain_mergeable_at(&[no, yes], B));
        assert!(!chain_mergeable_at(&[yes, m_left], B));
    }

    #[test]
    fn a_chain_of_increments_reaches_the_full_review() {
        let l1 = line("通過", A, "0/0/1/0", "全文");
        let l2 = line("不通過", B, "0/1/0/0", &std::format!("增量 {A}..{B}"));
        let l3 = line("通過", C, "0/0/0/1", &std::format!("增量 {B}..{C}"));
        let short = line("通過", C, "0/0/0/0", "增量 967111ff..2af03401");
        let rs = [by("a9136", &l1), by("1ced7", &l2), by("1ced7", &l3)];
        assert!(chain_mergeable_at(&rs, C));
        // drop the middle link: B..C hangs on nothing
        assert!(!chain_mergeable_at(&[rs[0], rs[2]], C));
        // the refused middle link is picked up by someone else: broken
        assert!(!chain_mergeable_at(&[rs[0], rs[1], by("bb7dc", &l3)], C));
        // a short range is not a link and not a pass
        assert!(!chain_mergeable_at(&[rs[0], by("1ced7", &short)], C));
    }

    #[test]
    fn a_cycle_of_ranges_is_not_a_chain() {
        let ab = line("通過", B, "0/0/0/0", &std::format!("增量 {A}..{B}"));
        let ba = line("通過", A, "0/0/0/0", &std::format!("增量 {B}..{A}"));
        let rs = [by("x", &ab), by("x", &ba)];
        assert!(!chain_mergeable_at(&rs, B));
        assert!(!chain_mergeable_at(&rs, A));
    }

    #[test]
    fn a_byte_order_mark_is_not_part_of_the_label() {
        let file = std::format!("\u{feff}{}\n", review("全文"));
        assert_eq!(parse_review(&file).unwrap().head, B);
        let v = std::format!("\u{feff}驗收: 通過 頭: {B} 機: m 樣本: s 沒驗: 無");
        assert_eq!(parse_verify(&v).unwrap().head, B);
    }

    #[test]
    fn a_skipped_list_that_mentions_the_marker_is_kept_whole() {
        let line = std::format!("驗收: 通過 頭: {B} 機: m 樣本: s 沒驗: Windows 沒驗: 版 Linux");
        let v = parse_verify(&line).unwrap();
        assert_eq!(v.samples, "s");
        assert_eq!(v.not_checked, Some("Windows 沒驗: 版 Linux"));
    }

    #[test]
    fn a_verify_line_by_the_rule_parses() {
        let line = std::format!(
            "驗收: 通過 頭: {B} 機: mac-b,mac-a 樣本: queue mac-b 1 + 桌機 4 沒驗: 卡名額時 GET 的 reason、真 claude 卡名額"
        );
        let v = parse_verify(&line).unwrap();
        assert_eq!(v.verdict, Verdict::Pass);
        assert_eq!(v.head, B);
        assert_eq!(v.host, "mac-b,mac-a");
        assert_eq!(v.samples, "queue mac-b 1 + 桌機 4");
        assert_eq!(
            v.not_checked,
            Some("卡名額時 GET 的 reason、真 claude 卡名額")
        );
    }

    #[test]
    fn nothing_skipped_is_written_as_none() {
        let line = std::format!("驗收: 不通過 頭: {B} 機: mac-a.home 樣本: queue 5 沒驗: 無");
        let v = parse_verify(&line).unwrap();
        assert_eq!(v.verdict, Verdict::Fail);
        assert_eq!(v.not_checked, None);
    }

    #[test]
    fn the_old_verify_formats_are_refused() {
        for line in [
            std::format!("驗收:通過(上限 1)/ 頭 {B} / 機 桌機 / 樣本 10 / 沒驗:上限 10"),
            std::format!("驗收: 不過 頭: {B} 機: m 樣本: s 沒驗: 無"),
            std::format!("驗收: 通過 頭: {B} 機: Andy 的 Mac 樣本: s 沒驗: 無"),
            std::format!("驗收: 通過 頭: {B} 機: m 樣本: s"),
            std::format!("驗收: 通過 頭: {B} 機: m 樣本: s 沒驗: "),
        ] {
            assert!(parse_verify(&line).is_err(), "{line}");
        }
    }
}
