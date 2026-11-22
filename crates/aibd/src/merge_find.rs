//! aibd §8 合併類似, slice 8a: find the personas / second brains / roles whose names are
//! nearly the same (DESIGN-merge-6770f-1123.md §2). Pure functions only: no store, no files,
//! nothing changes here; the act that lists the groups and the preview read through it.
//!
//! The rule (SPEC-ai-partner-open-ebab8-1122.md §5, DECISION-council-aipartner-ebab8-1122.md
//! 第五節 B):
//! - a name is normalised (full-width letters and digits made plain, lower case, a trailing
//!   `-2` / `_2` / `(2)` dropped, "專案" / "project" dropped, spaces and punctuation dropped);
//! - two names score 1.0 when normalised the same, 0.9 when the shorter (≥ 4 chars, at least
//!   half the longer) is inside the longer, else the Dice score of their character pairs;
//! - pairs at ≥ 0.70 join one group; a group whose every pair is ≥ 0.85 is "nearly the same"
//!   (merged by default), any other group is "maybe alike" (nothing ticked by default);
//! - two names that each carry a known customer / project prefix, and the prefixes differ,
//!   never join (`吳軒-客服` and `心+-客服` are two different people's helpers);
//! - `X` and `X-2` are the same name, merged by default, keeping the one without `-2`.
//!
//! The one kept by default: a built-in item always; else the most used, then the one without
//! a `-number` tail, then the longer normalised name, then the smaller uid (the same answer
//! every time).

use std::collections::BTreeMap;

/// Pairs at or over this join one group.
pub const LINK_AT: f64 = 0.70;
/// A group whose every pair is at or over this is merged by default.
pub const SURE_AT: f64 = 0.85;
/// The most items of one kind looked at (the registry's own list limit).
pub const ITEMS_MAX: usize = 200;

/// One persona / brain / role as the finder sees it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Item {
    pub uid: String,
    pub name: String,
    /// How many seats / personas / assignments use it (who is kept by default).
    pub used: u32,
    /// A built-in item (the six roles): never merged away, always the one kept.
    pub builtin: bool,
}

/// One group of alike names.
#[derive(Debug, Clone, PartialEq)]
pub struct Group {
    /// The items, in the order they were given.
    pub items: Vec<Item>,
    /// The uid kept by default.
    pub keep: String,
    /// Every pair is ≥ [`SURE_AT`]: the others are ticked to merge by default.
    pub sure: bool,
    /// The lowest pair score inside the group (shown as "how alike").
    pub low: f64,
}

impl Group {
    /// The uids ticked to merge by default: every non-built-in one but the kept one when
    /// the group is sure, none otherwise.
    pub fn ticked(&self) -> Vec<&str> {
        if !self.sure {
            return Vec::new();
        }
        self.items
            .iter()
            .filter(|x| x.uid != self.keep && !x.builtin)
            .map(|x| x.uid.as_str())
            .collect()
    }
}

/// Full-width ASCII (U+FF01..=U+FF5E) and the ideographic space made plain; the rest kept.
fn plain(c: char) -> char {
    match c {
        '\u{3000}' => ' ',
        '\u{FF01}'..='\u{FF5E}' => char::from_u32(c as u32 - 0xFEE0).unwrap_or(c),
        _ => c,
    }
}

/// Spaces and the punctuation names are written with.
fn is_sep(c: char) -> bool {
    c.is_whitespace()
        || matches!(
            c,
            '.' | '·'
                | '('
                | ')'
                | '|'
                | '-'
                | '—'
                | '–'
                | '_'
                | '、'
                | ','
                | ':'
                | '/'
                | '+'
                | '['
                | ']'
                | '「'
                | '」'
                | '《'
                | '》'
                | '!'
                | '?'
                | '。'
        )
}

/// `name` without a trailing `-2` / `_2` / ` 2` / `(2)` (any run of ASCII digits).
fn strip_number_tail(s: &str) -> &str {
    let t = s.trim_end();
    let (body, closed) = match t.strip_suffix(')') {
        Some(b) => (b, true),
        None => (t, false),
    };
    let digits = body.len() - body.trim_end_matches(|c: char| c.is_ascii_digit()).len();
    if digits == 0 {
        return s;
    }
    let head = &body[..body.len() - digits];
    let cut = if closed {
        head.strip_suffix('(')
    } else {
        head.strip_suffix(['-', '_', ' '])
    };
    match cut {
        Some(h) if !h.trim().is_empty() => h,
        _ => s,
    }
}

/// `true` when the name ends in a `-number` tail (`X-2`, `X_3`, `X (2)`).
pub fn has_number_tail(name: &str) -> bool {
    let p: String = name.chars().map(plain).collect();
    strip_number_tail(&p).len() != p.len()
}

/// The normalised name the scores are taken on.
pub fn norm(name: &str) -> String {
    let p: String = name.chars().map(plain).collect::<String>().to_lowercase();
    let p = strip_number_tail(&p)
        .replace("專案", "")
        .replace("project", "");
    p.chars()
        .filter(|c| !is_sep(*c) && !c.is_control())
        .collect()
}

fn pairs(chars: &[char]) -> Vec<(char, char)> {
    let mut v: Vec<(char, char)> = chars.windows(2).map(|w| (w[0], w[1])).collect();
    v.sort_unstable();
    v.dedup();
    v
}

/// How alike two names are, 0.0..=1.0.
pub fn score(a: &str, b: &str) -> f64 {
    let (x, y) = (norm(a), norm(b));
    if x.is_empty() || y.is_empty() {
        return 0.0;
    }
    if x == y {
        return 1.0;
    }
    let (xc, yc): (Vec<char>, Vec<char>) = (x.chars().collect(), y.chars().collect());
    let (sh, lo) = if xc.len() <= yc.len() {
        (&x, &y)
    } else {
        (&y, &x)
    };
    let (sl, ll) = (sh.chars().count(), lo.chars().count());
    if sl >= 4 && lo.contains(sh.as_str()) && sl * 2 >= ll {
        return 0.9;
    }
    let (pa, pb) = (pairs(&xc), pairs(&yc));
    if pa.is_empty() && pb.is_empty() {
        return 0.0;
    }
    let both = pa.iter().filter(|p| pb.binary_search(p).is_ok()).count();
    (2 * both) as f64 / (pa.len() + pb.len()) as f64
}

/// The known prefix a name starts with, if any: the longest of `prefixes` that the name
/// starts with and that is followed by a separator. Compared normalised-case.
fn prefix_of<'a>(name: &str, prefixes: &'a [String]) -> Option<&'a str> {
    let n: String = name.chars().map(plain).collect::<String>().to_lowercase();
    prefixes
        .iter()
        .filter(|p| !p.trim().is_empty())
        .filter(|p| {
            let p = p.chars().map(plain).collect::<String>().to_lowercase();
            n.strip_prefix(p.as_str())
                .and_then(|rest| rest.chars().next())
                .is_some_and(is_sep)
        })
        .max_by_key(|p| p.chars().count())
        .map(String::as_str)
}

/// Two names with two different known prefixes never join.
fn other_customers(a: &str, b: &str, prefixes: &[String]) -> bool {
    match (prefix_of(a, prefixes), prefix_of(b, prefixes)) {
        (Some(x), Some(y)) => norm(x) != norm(y),
        _ => false,
    }
}

fn find(par: &mut [usize], i: usize) -> usize {
    let mut r = i;
    while par[r] != r {
        r = par[r];
    }
    let mut c = i;
    while par[c] != r {
        let n = par[c];
        par[c] = r;
        c = n;
    }
    r
}

/// The uid kept by default among `items` (see the module note).
pub fn default_keep(items: &[Item]) -> Option<&str> {
    if let Some(b) = items
        .iter()
        .filter(|x| x.builtin)
        .min_by(|a, b| a.uid.cmp(&b.uid))
    {
        return Some(&b.uid);
    }
    items
        .iter()
        .min_by(|a, b| {
            b.used
                .cmp(&a.used)
                .then(has_number_tail(&a.name).cmp(&has_number_tail(&b.name)))
                .then(
                    norm(&b.name)
                        .chars()
                        .count()
                        .cmp(&norm(&a.name).chars().count()),
                )
                .then(a.uid.cmp(&b.uid))
        })
        .map(|x| x.uid.as_str())
}

/// The groups of alike names among `items` (at most [`ITEMS_MAX`] looked at; the caller
/// passes only live ones, not archived or aliased away). `prefixes` = the known customer /
/// project names. A group of built-in items only is not listed (nothing could merge).
pub fn find_similar(items: &[Item], prefixes: &[String]) -> Vec<Group> {
    let items = &items[..items.len().min(ITEMS_MAX)];
    let n = items.len();
    let mut par: Vec<usize> = (0..n).collect();
    let mut sc = vec![vec![0.0f64; n]; n];
    for i in 0..n {
        for j in i + 1..n {
            let v = if other_customers(&items[i].name, &items[j].name, prefixes) {
                0.0
            } else {
                score(&items[i].name, &items[j].name)
            };
            sc[i][j] = v;
            sc[j][i] = v;
            if v >= LINK_AT {
                let (a, b) = (find(&mut par, i), find(&mut par, j));
                if a != b {
                    par[a] = b;
                }
            }
        }
    }
    let mut by_root: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
    for i in 0..n {
        let r = find(&mut par, i);
        by_root.entry(r).or_default().push(i);
    }
    let mut out: Vec<Group> = by_root
        .into_values()
        .filter(|ix| ix.len() > 1 && ix.iter().any(|&i| !items[i].builtin))
        .map(|ix| {
            let mut low = 1.0f64;
            for (k, &i) in ix.iter().enumerate() {
                for &j in &ix[k + 1..] {
                    low = low.min(sc[i][j]);
                }
            }
            let g: Vec<Item> = ix.iter().map(|&i| items[i].clone()).collect();
            let keep = default_keep(&g).unwrap_or_default().to_string();
            Group {
                items: g,
                keep,
                sure: low >= SURE_AT,
                low,
            }
        })
        .collect();
    // the first item's place in the input orders the groups (stable for the page)
    out.sort_by_key(|g| {
        items
            .iter()
            .position(|x| x.uid == g.items[0].uid)
            .unwrap_or(usize::MAX)
    });
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn it(uid: &str, name: &str, used: u32) -> Item {
        Item {
            uid: uid.into(),
            name: name.into(),
            used,
            builtin: false,
        }
    }

    #[test]
    fn mg1_a_dash_2_twin_is_the_same_name_merged_keeping_the_plain_one() {
        assert_eq!(norm("AIB2主線核心架構師-2"), norm("AIB2主線核心架構師"));
        assert_eq!(score("AIB2主線核心架構師-2", "AIB2主線核心架構師"), 1.0);
        let g = find_similar(
            &[
                it("aaaaa1", "AIB2主線核心架構師-2", 0),
                it("aaaaa2", "AIB2主線核心架構師", 0),
            ],
            &[],
        );
        assert_eq!(g.len(), 1);
        assert!(g[0].sure);
        assert_eq!(g[0].keep, "aaaaa2");
        assert_eq!(g[0].ticked(), vec!["aaaaa1"]);
    }

    #[test]
    fn mg1_the_tail_forms_and_what_is_not_a_tail() {
        for t in [
            "X測試員-2",
            "X測試員_3",
            "X測試員 2",
            "X測試員(2)",
            "X測試員(2)",
            "X測試員－２",
        ] {
            assert!(has_number_tail(t), "{t}");
            assert_eq!(norm(t), "x測試員", "{t}");
        }
        // a name that is only digits, or digits inside a word, is not a tail
        assert!(!has_number_tail("2"));
        assert!(!has_number_tail("AIB2"));
        assert_eq!(norm("AIB2"), "aib2");
    }

    #[test]
    fn mg1_used_more_beats_no_tail() {
        let g = find_similar(
            &[it("bbbbb1", "審查員-2", 3), it("bbbbb2", "審查員", 1)],
            &[],
        );
        assert_eq!(g[0].keep, "bbbbb1");
    }

    #[test]
    fn mg2_two_customers_prefixes_never_join_one_side_prefixed_still_compares() {
        let pre = vec!["吳軒".to_string(), "心+".to_string()];
        let g = find_similar(
            &[
                it("c00001", "吳軒-AIB客服助理", 0),
                it("c00002", "心+-AIB客服助理", 0),
            ],
            &pre,
        );
        // without the prefix rule these two score ≥ LINK_AT (12/15 = 0.8) and would join
        assert!(score("吳軒-AIB客服助理", "心+-AIB客服助理") >= LINK_AT);
        assert!(g.is_empty(), "{g:?}");
        let g = find_similar(
            &[it("c00001", "客服助理", 0), it("c00002", "心+-客服助理", 0)],
            &pre,
        );
        assert_eq!(g.len(), 1);
        // not a known prefix: part of the name, compared as usual
        let g = find_similar(
            &[
                it("c00001", "甲-客服助理", 0),
                it("c00002", "乙-客服助理", 0),
            ],
            &pre,
        );
        assert_eq!(g.len(), 1);
    }

    #[test]
    fn mg3_maybe_alike_is_listed_with_nothing_ticked() {
        let (a, b) = ("AIB2測試流程工程師", "AIB2測試工程師");
        let v = score(a, b);
        assert!((LINK_AT..SURE_AT).contains(&v), "{v}");
        let g = find_similar(&[it("d00001", a, 0), it("d00002", b, 0)], &[]);
        assert_eq!(g.len(), 1);
        assert!(!g[0].sure);
        assert!(g[0].ticked().is_empty());
    }

    #[test]
    fn mg3_unalike_names_make_no_group() {
        assert!(score("頁面工程師", "審查員") < LINK_AT);
        assert!(find_similar(
            &[it("e00001", "頁面工程師", 0), it("e00002", "審查員", 0)],
            &[]
        )
        .is_empty());
    }

    #[test]
    fn mg4_a_built_in_is_kept_never_ticked_and_built_ins_alone_are_not_listed() {
        let mut r = it("author", "作者", 0);
        r.builtin = true;
        let g = find_similar(&[it("rol_a1", "作者-2", 5), r.clone()], &[]);
        assert_eq!(g[0].keep, "author");
        assert_eq!(g[0].ticked(), vec!["rol_a1"]);
        let mut r2 = it("author2", "作者", 0);
        r2.builtin = true;
        assert!(find_similar(&[r, r2], &[]).is_empty());
    }

    #[test]
    fn project_word_and_full_width_are_ignored() {
        assert_eq!(norm("ＡＩＢ２ 重構專案"), norm("aib2重構"));
        assert_eq!(norm("Ez2 Project"), "ez2");
        assert_eq!(score("", "x"), 0.0);
        assert_eq!(score("---", "x"), 0.0);
    }

    #[test]
    fn contained_name_scores_point_nine() {
        assert_eq!(score("AIB2.0跨平台重構", "AIB2.0跨平台重構筆記"), 0.9);
        // too short to count as contained
        assert!(score("重構", "AIB2.0跨平台重構") < SURE_AT);
    }

    #[test]
    fn a_chain_group_is_maybe_unless_every_pair_is_sure() {
        let g = find_similar(
            &[
                it("f00001", "核心架構師", 0),
                it("f00002", "核心架構師-2", 0),
                it("f00003", "核心架構師助理", 0),
            ],
            &[],
        );
        // every pair here is ≥ SURE_AT (same / contained), so the group is sure
        assert_eq!(g.len(), 1);
        assert_eq!(g[0].items.len(), 3);
        assert!(g[0].sure);
        // a chain with one pair under SURE_AT (0.70..0.85) is "maybe alike": nothing ticked
        // (AIB2測試工程師 / -2 = 1.0; either vs AIB2測試流程工程師 = 14/18 ≈ 0.78)
        let g = find_similar(
            &[
                it("f00011", "AIB2測試工程師", 0),
                it("f00012", "AIB2測試工程師-2", 0),
                it("f00013", "AIB2測試流程工程師", 0),
            ],
            &[],
        );
        assert_eq!(g.len(), 1);
        assert_eq!(g[0].items.len(), 3);
        assert!(!g[0].sure, "{g:?}");
        assert!((LINK_AT..SURE_AT).contains(&g[0].low), "{}", g[0].low);
        assert!(g[0].ticked().is_empty());
        // used ties, the ones without -2 tie, the longer normalised name is kept
        assert_eq!(g[0].keep, "f00013");
    }

    #[test]
    fn the_same_input_gives_the_same_answer() {
        let l = [
            it("g00002", "審查員", 0),
            it("g00001", "審查員-2", 0),
            it("g00003", "審查員-3", 0),
        ];
        let a = find_similar(&l, &[]);
        let b = find_similar(&l, &[]);
        assert_eq!(a, b);
        assert_eq!(a[0].keep, "g00002");
    }

    #[test]
    fn at_most_items_max_are_looked_at() {
        let l: Vec<Item> = (0..ITEMS_MAX + 5)
            .map(|i| it(&format!("h{i:05}"), "同名", 0))
            .collect();
        let g = find_similar(&l, &[]);
        assert_eq!(g[0].items.len(), ITEMS_MAX);
    }
}
