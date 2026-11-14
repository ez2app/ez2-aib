//! Stale-head gate (MVP-1). GitHub is a map of path → answer; a path not in
//! the map is an error, as an unreachable GitHub would be.

use aib_drive::snapshot::Read;
use aib_stalehead::{check, review_first_line, review_paths, tokens, Quota, Verdict};
use serde_json::{json, Value};
use std::cell::RefCell;
use std::collections::BTreeMap;

const REPO: &str = "o/r";
const NEW: &str = "e8e61c1cbff858bf6d9b10fafc47c08acded6739";
const OLD: &str = "c908ce78f864fda6fdc40f4df173d2acafd2f61c";
const OTHER: &str = "81074c922058a6ec7263ced4ef772f8280fa202e";
/// #684 (under #685): its head, and an older commit of it.
const LOW: &str = "728c663a0000000000000000000000000000beef";
const LOW_OLD: &str = "cf850816000000000000000000000000000dead1";

struct Gh {
    answers: BTreeMap<String, Result<Value, String>>,
    calls: RefCell<Vec<String>>,
}

impl Read for Gh {
    fn get(&self, path: &str) -> Result<Value, String> {
        self.calls.borrow_mut().push(path.to_owned());
        self.answers
            .get(path)
            .cloned()
            .unwrap_or_else(|| Err(format!("gh api {path}: exit 1: connection refused")))
    }
}

struct Q(Option<u64>);

impl Quota for Q {
    fn remaining(&self) -> Option<u64> {
        self.0
    }
}

const OK: Q = Q(Some(4000));

/// #685 open at NEW (OLD is its earlier commit); #690 open at OTHER.
fn gh() -> Gh {
    let mut a = BTreeMap::new();
    a.insert(
        format!("repos/{REPO}/pulls?state=open&per_page=100"),
        Ok(json!([
            {"number": 685, "head": {"sha": NEW}},
            {"number": 690, "head": {"sha": OTHER}},
            {"number": 684, "head": {"sha": LOW}},
        ])),
    );
    a.insert(
        format!("repos/{REPO}/commits/{}/pulls", &OLD[..8]),
        Ok(json!([{"number": 685, "state": "open"}])),
    );
    a.insert(
        format!("repos/{REPO}/commits/{OLD}/pulls"),
        Ok(json!([{"number": 685, "state": "open"}])),
    );
    // GitHub ties a PR's head commit to the PR too: only the open list tells
    // "current" from "stale"
    for h in [&NEW[..8], &NEW[..12], NEW] {
        a.insert(
            format!("repos/{REPO}/commits/{h}/pulls"),
            Ok(json!([{"number": 685, "state": "open"}])),
        );
    }
    // stacked: a commit of the lower PR belongs to the upper one too (a9136
    // checked 728c663a ⇒ #684, #685 on GitHub)
    a.insert(
        format!("repos/{REPO}/commits/{}/pulls", &LOW_OLD[..8]),
        Ok(json!([{"number": 684, "state": "open"}, {"number": 685, "state": "open"}])),
    );
    // on main: GitHub names only the merged PR that brought it
    a.insert(
        format!("repos/{REPO}/commits/5806dac2/pulls"),
        Ok(json!([{"number": 687, "state": "closed"}])),
    );
    a.insert(
        format!("repos/{REPO}/commits/0badc0de/pulls"),
        Err("gh api repos/o/r/commits/0badc0de/pulls: exit 1: gh: No commit found for SHA: 0badc0de (HTTP 422)".to_owned()),
    );
    Gh {
        answers: a,
        calls: RefCell::new(Vec::new()),
    }
}

fn verdicts(r: &aib_stalehead::Report) -> Vec<(&str, &'static str)> {
    r.findings
        .iter()
        .map(|(t, v)| (t.hex.as_str(), v.code()))
        .collect()
}

/// SL3: a dm that names a PR's old head is held back, and says the current one.
#[test]
fn a_dm_naming_an_old_head_is_held_back() {
    let r = check(&gh(), &OK, REPO, &format!("#685 請審，頭 {}", &OLD[..8]));
    assert!(r.blocked(), "{r:?}");
    assert_eq!(
        r.findings[0].1,
        Verdict::StaleHead {
            heads: vec![(685, NEW.to_owned())]
        }
    );
    assert_eq!(r.to_json()["reason"], "stale_head");
    assert!(r.lines()[0].contains(&NEW[..8]), "{:?}", r.lines());
}

/// The current head — full or any prefix of 8 or more — is never held back,
/// and needs no lookup beyond the open list.
#[test]
fn the_current_head_is_never_held_back() {
    for text in [
        format!("#685 頭 {NEW}"),
        format!("#685 頭 {}", &NEW[..8]),
        format!("#685 頭 {}", &NEW[..12]),
        format!("結論: 通過 頭: {NEW} H/M/L/N: 0/0/0/0 範圍: 全文"),
    ] {
        let g = gh();
        let r = check(&g, &OK, REPO, &text);
        assert!(!r.blocked(), "{text}: {r:?}");
        let codes: Vec<&str> = verdicts(&r).into_iter().map(|(_, c)| c).collect();
        assert_eq!(codes, vec!["current"], "{text}");
        assert_eq!(g.calls.borrow().len(), 1, "{text}: {:?}", g.calls.borrow());
    }
}

/// An increment names its base on purpose: `<old>..<new>` passes.
#[test]
fn a_range_base_is_not_held_back() {
    let r = check(
        &gh(),
        &OK,
        REPO,
        &format!("結論: 通過 頭: {NEW} H/M/L/N: 0/0/0/0 範圍: 增量 {OLD}..{NEW}"),
    );
    assert!(!r.blocked(), "{r:?}");
    assert!(verdicts(&r).contains(&(OLD, "stale_named")), "{r:?}");
}

/// The base alone, written as the head, is still held back.
#[test]
fn a_range_base_used_as_a_head_elsewhere_is_held_back() {
    let r = check(
        &gh(),
        &OK,
        REPO,
        &format!("範圍 {OLD}..{OTHER};#685 頭 {OLD}"),
    );
    assert!(r.blocked(), "{r:?}");
}

/// Stacked PRs: an old commit of the lower PR, beside the upper PR's current
/// head, is not held back — and every PR it belongs to is listed (a9136 M1).
#[test]
fn stacked_prs_an_old_lower_commit_beside_the_upper_head_is_not_held() {
    let r = check(
        &gh(),
        &OK,
        REPO,
        &format!(
            "#685 現頭 {},底下 #684 頭 {} 已換掉",
            &NEW[..8],
            &LOW_OLD[..8]
        ),
    );
    assert!(!r.blocked(), "{r:?}");
    assert_eq!(
        r.findings[1].1,
        Verdict::StaleNamed {
            heads: vec![(684, LOW.to_owned()), (685, NEW.to_owned())]
        }
    );
    // alone, written as a head, it is held back, and both PRs are named
    let r = check(&gh(), &OK, REPO, &format!("#684 頭 {}", &LOW_OLD[..8]));
    assert!(r.blocked(), "{r:?}");
    let lines = r.lines();
    let l = &lines[0];
    assert!(l.contains("#684") && l.contains("#685"), "{l}");
}

/// An old commit named as a fix, not as a head, is a reminder (a9136 L1).
#[test]
fn an_old_commit_not_written_as_a_head_is_a_reminder() {
    for text in [
        format!("#685 根因修在 {}", &OLD[..8]),
        format!("#685 舊頭 {} 已換掉", &OLD[..8]),
        format!("ahead {}", &OLD[..8]),
        format!("overhead {}", &OLD[..8]),
        format!("增量 {}..{NEW}", &OLD[..8]),
    ] {
        let r = check(&gh(), &OK, REPO, &text);
        assert!(!r.blocked(), "{text}: {r:?}");
        assert!(!r.lines().is_empty(), "{text}: still shown");
    }
    let r = check(&gh(), &OK, REPO, &format!("#685 根因修在 {}", &OLD[..8]));
    assert_eq!(r.findings[0].1.code(), "stale_mention");
}

/// Where a head goes: after 頭 / 頭: / head / HEAD= / 推了, or right of `..`.
#[test]
fn every_head_position_is_held_to_the_current_head() {
    let o = &OLD[..8];
    for text in [
        format!("頭:{o}"),
        format!("頭: {o}"),
        format!("頭碼 {o}"),
        format!("HEAD={o}"),
        format!("head {o}"),
        format!("推了 {o}"),
        format!("#685 已推 {o}"),
        format!("推上 {o}"),
        format!("現在頭 {o}"),
        format!("增量 {OTHER}..{o}"),
    ] {
        let r = check(&gh(), &OK, REPO, &text);
        assert!(r.blocked(), "{text}: {r:?}");
    }
}

/// "was OLD, now NEW": the text knows the current head.
#[test]
fn an_old_commit_beside_its_current_head_is_shown_not_held() {
    let r = check(
        &gh(),
        &OK,
        REPO,
        &format!("#685 遠端從 {} 推到 {}", &OLD[..8], &NEW[..8]),
    );
    assert!(!r.blocked(), "{r:?}");
    assert_eq!(
        verdicts(&r),
        vec![(&OLD[..8], "stale_named"), (&NEW[..8], "current")]
    );
    assert!(!r.lines().is_empty(), "the old one is still shown");
}

/// The current head of *another* PR does not excuse an old head.
#[test]
fn another_prs_head_does_not_excuse_an_old_head() {
    let r = check(
        &gh(),
        &OK,
        REPO,
        &format!("#690 頭 {} / #685 頭 {}", &OTHER[..8], &OLD[..8]),
    );
    assert!(r.blocked(), "{r:?}");
}

/// Not pushed yet (or mistyped): a reminder, not a block.
#[test]
fn a_commit_github_does_not_know_is_a_reminder() {
    let r = check(&gh(), &OK, REPO, "本機頭 0badc0de 還沒推");
    assert!(!r.blocked());
    assert_eq!(verdicts(&r), vec![("0badc0de", "not_on_github")]);
    assert!(r.lines()[0].contains("找不到"));
}

/// On main (only a merged PR is tied to it): fine, silent.
#[test]
fn a_commit_on_main_is_fine() {
    let r = check(&gh(), &OK, REPO, "main 5806dac2");
    assert!(!r.blocked());
    assert_eq!(verdicts(&r), vec![("5806dac2", "no_open_pr")]);
    assert!(r.lines().is_empty());
}

/// A lookup that fails is unverified — neither fine nor stale.
#[test]
fn a_failed_lookup_is_unverified_not_fine() {
    let r = check(&gh(), &OK, REPO, "看 deadbeef01 這個");
    assert!(!r.blocked());
    assert_eq!(verdicts(&r), vec![("deadbeef01", "unverified")]);
    assert!(r.lines()[0].contains("未核"));
}

/// An unreadable or possibly partial open list: everything unverified, nothing held.
#[test]
fn an_unreadable_open_list_leaves_everything_unverified() {
    let mut g = gh();
    g.answers.insert(
        format!("repos/{REPO}/pulls?state=open&per_page=100"),
        Ok(Value::Array(
            (0..100)
                .map(|n| json!({"number": n, "head": {"sha": NEW}}))
                .collect(),
        )),
    );
    let r = check(&g, &OK, REPO, &format!("#685 頭 {}", &OLD[..8]));
    assert!(!r.blocked());
    assert_eq!(verdicts(&r), vec![(&OLD[..8], "unverified")]);
}

/// Low or unreadable quota: no GitHub call at all, only a reminder.
#[test]
fn low_or_unknown_quota_only_reminds_and_reads_nothing() {
    for q in [Q(Some(1499)), Q(None)] {
        let g = gh();
        let r = check(&g, &q, REPO, &format!("#685 頭 {}", &OLD[..8]));
        assert!(!r.blocked(), "{r:?}");
        assert!(!r.enforce);
        assert!(g.calls.borrow().is_empty(), "{:?}", g.calls.borrow());
        assert!(r.lines()[0].contains("只提醒"), "{:?}", r.lines());
    }
    // the floor itself enforces
    assert!(check(&gh(), &Q(Some(1500)), REPO, &format!("頭 {}", &OLD[..8])).blocked());
}

/// No hex in the text: no call, not even the quota.
#[test]
fn a_text_without_heads_reads_nothing() {
    let g = gh();
    let r = check(&g, &Q(None), REPO, "收到，稍後回");
    assert!(!r.blocked() && r.enforce && r.findings.is_empty());
    assert!(g.calls.borrow().is_empty());
}

/// Only lone 8–40 lowercase hex runs with a letter count.
#[test]
fn what_counts_as_a_head() {
    let t: Vec<String> = tokens(
        "q_812cfc2d5b53cc42 Q_51EA1FEB 20261111 1794413580 abc1234 e8e61c1c E8E61C1C \
         x81074c92 81074c92y https://github.com/o/r/commit/c908ce78 a31e7f59, e8e61c1c",
    )
    .into_iter()
    .map(|t| t.hex)
    .collect();
    assert_eq!(t, vec!["e8e61c1c", "c908ce78", "a31e7f59"]);
    let long = "a".repeat(41);
    assert!(tokens(&long).is_empty());
    assert_eq!(tokens(&"a1".repeat(20)).len(), 1);
}

/// More than ten lookups: the rest are unverified, and no more calls.
#[test]
fn lookups_stop_at_ten() {
    let g = gh();
    let text: String = (0..12).map(|i| format!("dead{i:04}ab ")).collect();
    let r = check(&g, &OK, REPO, &text);
    assert_eq!(g.calls.borrow().len(), 1 + 10);
    assert_eq!(r.findings.len(), 12);
    assert_eq!(r.findings[11].1.code(), "unverified");
}

/// A dm hands over a review: its path is found, its first line is what is checked.
#[test]
fn a_review_named_in_a_dm_is_found_and_only_its_first_line_counts() {
    let dm = "審完:/Users/x/.ez2-aib/pending/REVIEW-685-r4-1ced7-e8e61c1c.md(r4)、~/p/VERIFY-a.md。另見 REVIEW-bare.md";
    // the same with full-width colon and brackets
    let wide = "審完\u{ff1a}/Users/x/.ez2-aib/pending/REVIEW-685-r4-1ced7-e8e61c1c.md\u{ff08}r4\u{ff09}、~/p/VERIFY-a.md。";
    assert_eq!(review_paths(wide), review_paths(dm));
    assert_eq!(
        review_paths(dm),
        vec![
            "/Users/x/.ez2-aib/pending/REVIEW-685-r4-1ced7-e8e61c1c.md",
            "~/p/VERIFY-a.md"
        ]
    );
    let file =
        format!("\u{feff}結論: 通過 頭: {OLD} H/M/L/N: 0/0/0/0 範圍: 全文\r\n本文提到 {NEW}\n");
    let first = review_first_line(&file);
    assert!(first.starts_with("結論") && !first.contains(NEW));
    assert!(check(&gh(), &OK, REPO, first).blocked());
}
