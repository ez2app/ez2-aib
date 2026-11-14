// SPDX-License-Identifier: Apache-2.0
//! 進度樹的「能不能開工」原料(S79 n-2a,DEV-S79-drive-next-519d0-1114.md r1 §3;計畫
//! pending/PLAN-s79-n2a-plan-gate-b433b-1114.md r2)。純邏輯，不讀檔、不跑指令。
//!
//! - 節點兩個欄位:`approved {at, by, pointer}`(沒有 = 還沒同意)與 `after`(前置清單)。
//! - [`check`]:讀檔時抓寫錯的欄位(前綴不認得、指到不存在的節點、指自己、成環、approved 缺欄 /
//!   出處格式不對)。只報不擋。
//! - [`stuck`]:座艙「卡在哪」= 等同意 → 前置沒完成 → 被 claim → 來源讀不到 → 可開工。
//!   `ready` 不存，每次算(S79 定案);讀不到 ≠ 沒有(來源讀不到就是「不明」,不當成可開工)。
//! - 只有寫了任一欄的節點才算([`gated`]);其他節點的文字 status 照舊給人看。

use crate::{Index, Plan, PASS};
use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::fmt;

/// `approved.by` 第一版只收這一個(519d0 11-14)
pub const APPROVER: &str = "Andy";

/// 一個前置
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub enum Dep {
    /// `node:<id>`:那個節點 status 是「實查通過」才算完成
    Node(String),
    /// `pr:<n>`:ez2-aib 的 PR 合了才算
    Pr(u32),
    /// `skills-pr:<n>`:ez2-skills 的 PR 合了才算
    SkillsPr(u32),
}

impl fmt::Display for Dep {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Dep::Node(id) => write!(f, "node:{id}"),
            Dep::Pr(n) => write!(f, "pr:{n}"),
            Dep::SkillsPr(n) => write!(f, "skills-pr:{n}"),
        }
    }
}

fn number(s: &str) -> Option<u32> {
    if s.is_empty() || !s.bytes().all(|b| b.is_ascii_digit()) || s.starts_with('0') {
        return None;
    }
    s.parse().ok()
}

/// 只收三種前綴;其他字串(含空白、大寫、前導 0)= None
pub fn parse_dep(s: &str) -> Option<Dep> {
    if let Some(id) = s.strip_prefix("node:") {
        let ok = !id.is_empty()
            && id
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_' || c == '.');
        return ok.then(|| Dep::Node(id.to_string()));
    }
    if let Some(n) = s.strip_prefix("skills-pr:") {
        return number(n).map(Dep::SkillsPr);
    }
    s.strip_prefix("pr:").and_then(number).map(Dep::Pr)
}

/// `approved.pointer` 的兩種寫法(519d0 11-14)
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Pointer {
    /// pending 檔名 + ` §…` 或 ` #編號`
    File(String),
    /// dm id:`q_` + 16 位小寫十六進位
    Dm,
}

pub fn parse_pointer(p: &str) -> Option<Pointer> {
    if let Some(hex) = p.strip_prefix("q_") {
        let ok = hex.len() == 16
            && hex
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b));
        return ok.then_some(Pointer::Dm);
    }
    let (name, rest) = p.split_once(' ')?;
    let file_ok = name.contains('.')
        && !name.starts_with('.')
        && !name.contains(['/', '\\'])
        && !name.contains("..");
    let rest_ok = match rest.strip_prefix('§') {
        Some(sec) => !sec.trim().is_empty(),
        None => rest.strip_prefix('#').and_then(number).is_some(),
    };
    (file_ok && rest_ok).then(|| Pointer::File(name.to_string()))
}

/// 寫了 `approved` 或 `after` 的節點才有「卡在哪」
pub fn gated(n: &crate::Node) -> bool {
    n.approved.is_some() || !n.after.is_empty()
}

/// 一條欄位錯
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub enum Problem {
    /// after 裡不認得的字串
    BadAfter { node: String, item: String },
    /// `node:` 指到不存在的節點
    NoSuchNode { node: String, target: String },
    /// `node:` 指自己
    SelfRef { node: String },
    /// 成環(環上的節點，照 id 排序)
    Cycle { nodes: Vec<String> },
    /// approved 子欄缺或空
    ApprovedMissing { node: String, field: &'static str },
    /// approved.by 不是 Andy
    ApprovedBy { node: String, by: String },
    /// approved.pointer 不是兩種寫法之一
    BadPointer { node: String, pointer: String },
    /// approved.pointer 指的 pending 檔不在
    PointerGone { node: String, file: String },
}

impl fmt::Display for Problem {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Problem::BadAfter { node, item } => write!(
                f,
                "{node}:after 裡「{item}」不認得(只收 node:<id> / pr:<數字> / skills-pr:<數字>)"
            ),
            Problem::NoSuchNode { node, target } => {
                write!(f, "{node}:after 指到不存在的節點 {target}")
            }
            Problem::SelfRef { node } => write!(f, "{node}:after 指到自己"),
            Problem::Cycle { nodes } => write!(f, "after 成環:{}", nodes.join(" → ")),
            Problem::ApprovedMissing { node, field } => {
                write!(f, "{node}:approved 少了 {field}(或是空的)")
            }
            Problem::ApprovedBy { node, by } => {
                write!(f, "{node}:approved.by 是「{by}」,只收 {APPROVER}")
            }
            Problem::BadPointer { node, pointer } => write!(
                f,
                "{node}:approved.pointer「{pointer}」格式不對(pending 檔名 + §段 或 #編號,或 dm id q_…)"
            ),
            Problem::PointerGone { node, file } => {
                write!(f, "{node}:approved.pointer 指的 pending/{file} 不在")
            }
        }
    }
}

/// 抓寫錯的欄位。`pending` = pending 資料夾裡的檔名(讀不到傳 None:不驗檔在不在，也不報錯)。
pub fn check(p: &Plan, pending: Option<&HashSet<String>>) -> Vec<Problem> {
    let ids: HashSet<&str> = p.nodes.iter().map(|n| n.id.as_str()).collect();
    let mut out = Vec::new();
    // node 前置的邊(只留指到存在、不是自己的)
    let mut edges: BTreeMap<&str, Vec<&str>> = BTreeMap::new();
    for n in &p.nodes {
        for item in &n.after {
            match parse_dep(item) {
                None => out.push(Problem::BadAfter {
                    node: n.id.clone(),
                    item: item.clone(),
                }),
                Some(Dep::Node(t)) if t == n.id => {
                    out.push(Problem::SelfRef { node: n.id.clone() })
                }
                Some(Dep::Node(t)) => match ids.get(t.as_str()) {
                    Some(&t) => edges.entry(n.id.as_str()).or_default().push(t),
                    None => out.push(Problem::NoSuchNode {
                        node: n.id.clone(),
                        target: t,
                    }),
                },
                Some(_) => {}
            }
        }
        if let Some(a) = &n.approved {
            out.extend(approval_problems(&n.id, a, pending));
        }
    }
    for c in cycles(&edges) {
        out.push(Problem::Cycle { nodes: c });
    }
    out
}

/// approved 欄的錯(check 報、stuck 當成沒同意都用這一支)
fn approval_problems(
    node: &str,
    a: &crate::Approved,
    pending: Option<&HashSet<String>>,
) -> Vec<Problem> {
    let mut out = Vec::new();
    for (field, v) in [("at", &a.at), ("by", &a.by), ("pointer", &a.pointer)] {
        if v.trim().is_empty() {
            out.push(Problem::ApprovedMissing {
                node: node.to_string(),
                field,
            });
        }
    }
    if !a.by.trim().is_empty() && a.by != APPROVER {
        out.push(Problem::ApprovedBy {
            node: node.to_string(),
            by: a.by.clone(),
        });
    }
    if !a.pointer.trim().is_empty() {
        match parse_pointer(&a.pointer) {
            None => out.push(Problem::BadPointer {
                node: node.to_string(),
                pointer: a.pointer.clone(),
            }),
            Some(Pointer::File(f)) => {
                if pending.is_some_and(|set| !set.contains(&f)) {
                    out.push(Problem::PointerGone {
                        node: node.to_string(),
                        file: f,
                    });
                }
            }
            Some(Pointer::Dm) => {}
        }
    }
    out
}

/// 找所有環(顯式堆疊，不遞迴);每個環照 id 排序、去重
fn cycles(edges: &BTreeMap<&str, Vec<&str>>) -> BTreeSet<Vec<String>> {
    // 0 沒看過、1 走訪中、2 走完
    let mut color: BTreeMap<&str, u8> = BTreeMap::new();
    let mut found = BTreeSet::new();
    for &start in edges.keys() {
        if color.get(start).copied().unwrap_or(0) != 0 {
            continue;
        }
        let mut path: Vec<&str> = vec![start];
        let mut next: Vec<usize> = vec![0];
        color.insert(start, 1);
        while let (Some(&at), Some(i)) = (path.last(), next.last_mut()) {
            let kids = edges.get(at).map(Vec::as_slice).unwrap_or(&[]);
            if *i >= kids.len() {
                color.insert(at, 2);
                path.pop();
                next.pop();
                continue;
            }
            let k = kids[*i];
            *i += 1;
            match color.get(k).copied().unwrap_or(0) {
                0 => {
                    color.insert(k, 1);
                    path.push(k);
                    next.push(0);
                }
                1 => {
                    if let Some(pos) = path.iter().position(|&x| x == k) {
                        let mut c: Vec<String> =
                            path[pos..].iter().map(|s| s.to_string()).collect();
                        c.sort();
                        found.insert(c);
                    }
                }
                _ => {}
            }
        }
    }
    found
}

/// 一個 PR 的狀態(讀不到 = 不在 map 裡)
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PrState {
    Merged,
    Open,
    Closed,
}

/// 「卡在哪」要用的外部來源(讀在 main.rs)
#[derive(Debug, Clone, Default)]
pub struct Sources {
    /// ez2-aib 的 PR 狀態;沒有這張 = 讀不到
    pub prs: BTreeMap<u32, PrState>,
    /// ez2-skills 的 PR 狀態;沒有這張 = 讀不到
    pub skills_prs: BTreeMap<u32, PrState>,
    /// 節點 id → claim 的席;None = 這個來源還沒有(S73h build-claims 未上線:不判、不擋)
    pub claims: Option<BTreeMap<String, String>>,
    /// pending 資料夾的檔名(驗 approved.pointer 指的檔在不在);None = 讀不到、不驗
    pub pending: Option<HashSet<String>>,
}

/// 「卡在哪」
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Stuck {
    /// 沒有 approved
    NotApproved,
    /// approved 欄寫錯(子欄缺 / by 不是 Andy / pointer 壞或檔不在):不當成已同意
    BadApproval,
    /// 還沒完成的前置(含指到不存在的節點、已關沒合的 PR)
    Before(Vec<String>),
    /// 被別席 claim
    Claimed(String),
    /// 讀不到的前置(PR 狀態讀不到)
    Unknown(Vec<String>),
    /// 前置都好、已同意、沒人 claim
    Ready,
}

impl Stuck {
    /// 樹上標題後面的短字
    pub fn short(&self) -> String {
        match self {
            Stuck::NotApproved => "等同意".into(),
            Stuck::BadApproval => "同意欄寫錯".into(),
            Stuck::Before(v) => format!("前置 {}", v.len()),
            Stuck::Claimed(s) => format!("被 {s} claim"),
            Stuck::Unknown(_) => "不明".into(),
            Stuck::Ready => "可開工".into(),
        }
    }

    /// 打開說明的一行
    pub fn detail(&self) -> String {
        match self {
            Stuck::NotApproved => "還沒有 Andy 同意(approved 欄)".into(),
            Stuck::BadApproval => "approved 欄寫錯,不當成已同意(aib-plan --check 看明細)".into(),
            Stuck::Before(v) => format!("前置還沒完成:{}", v.join("、")),
            Stuck::Claimed(s) => format!("已被 {s} claim"),
            Stuck::Unknown(v) => format!("讀不到前置狀態(不當成可開工):{}", v.join("、")),
            Stuck::Ready => "已同意、前置都完成、沒人 claim".into(),
        }
    }
}

/// 節點卡在哪;沒寫欄位的節點 = None(不顯示)
pub fn stuck(p: &Plan, ix: &Index, at: usize, src: &Sources) -> Option<Stuck> {
    let n = p.nodes.get(at)?;
    if !gated(n) {
        return None;
    }
    let Some(a) = &n.approved else {
        return Some(Stuck::NotApproved);
    };
    // 模糊的一律不轉成已同意(DEV-S79 §3;1ced7 n-2a M1)
    if !approval_problems(&n.id, a, src.pending.as_ref()).is_empty() {
        return Some(Stuck::BadApproval);
    }
    let (mut before, mut unknown) = (Vec::new(), Vec::new());
    for item in &n.after {
        let done = match parse_dep(item) {
            // 寫錯的前置(check 會報)當成沒完成:寧可少派，不派錯
            None => Some(false),
            Some(Dep::Node(id)) => Some(
                ix.by
                    .get(&id)
                    .and_then(|&i| p.nodes.get(i))
                    .is_some_and(|t| t.status.as_deref() == Some(PASS)),
            ),
            Some(Dep::Pr(k)) => src.prs.get(&k).map(|s| *s == PrState::Merged),
            Some(Dep::SkillsPr(k)) => src.skills_prs.get(&k).map(|s| *s == PrState::Merged),
        };
        match done {
            Some(true) => {}
            Some(false) => before.push(item.clone()),
            None => unknown.push(item.clone()),
        }
    }
    if !before.is_empty() {
        return Some(Stuck::Before(before));
    }
    if let Some(seat) = src.claims.as_ref().and_then(|c| c.get(&n.id)) {
        return Some(Stuck::Claimed(seat.clone()));
    }
    if !unknown.is_empty() {
        return Some(Stuck::Unknown(unknown));
    }
    Some(Stuck::Ready)
}

/// 所有寫了欄位的節點要讀的 PR 號(ez2-aib, ez2-skills)
pub fn wanted_prs(p: &Plan) -> (BTreeSet<u32>, BTreeSet<u32>) {
    let (mut a, mut s) = (BTreeSet::new(), BTreeSet::new());
    for item in p.nodes.iter().flat_map(|n| n.after.iter()) {
        match parse_dep(item) {
            Some(Dep::Pr(k)) => {
                a.insert(k);
            }
            Some(Dep::SkillsPr(k)) => {
                s.insert(k);
            }
            _ => {}
        }
    }
    (a, s)
}

/// `gh pr view --json state` 的 state 欄
pub fn parse_pr_state(json: &str) -> Option<PrState> {
    let v: serde_json::Value = serde_json::from_str(json).ok()?;
    match v.get("state")?.as_str()? {
        "MERGED" => Some(PrState::Merged),
        "OPEN" => Some(PrState::Open),
        "CLOSED" => Some(PrState::Closed),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;
    use crate::{index, parse};

    fn plan(nodes: &str) -> Plan {
        parse(&format!("{{\"nodes\":[{nodes}]}}")).unwrap()
    }

    const OK: &str = r#""approved":{"at":"11-14 12:3x","by":"Andy","pointer":"DEV-S79-drive-next-519d0-1114.md §決定"}"#;

    #[test]
    fn deps_take_three_prefixes_only() {
        assert_eq!(parse_dep("node:S76-1"), Some(Dep::Node("S76-1".into())));
        assert_eq!(parse_dep("pr:753"), Some(Dep::Pr(753)));
        assert_eq!(parse_dep("skills-pr:14"), Some(Dep::SkillsPr(14)));
        for bad in [
            "S76-1", "pr:", "pr:07", "pr:7a", "PR:7", "node:", "node:a b", "issue:3", "pr: 7",
        ] {
            assert_eq!(parse_dep(bad), None, "{bad}");
        }
    }

    #[test]
    fn pointers_are_a_pending_file_with_a_section_or_a_dm_id() {
        assert_eq!(
            parse_pointer("DEV-S79-drive-next-519d0-1114.md §決定"),
            Some(Pointer::File("DEV-S79-drive-next-519d0-1114.md".into()))
        );
        assert_eq!(
            parse_pointer("DEV-S81.md #3"),
            Some(Pointer::File("DEV-S81.md".into()))
        );
        assert_eq!(parse_pointer("q_3b983c44032072e6"), Some(Pointer::Dm));
        for bad in [
            "Andy 說可以",
            "DEV.md",
            "DEV.md §",
            "DEV.md #x",
            "../x.md §1",
            "a/b.md §1",
            "q_3B983C44032072E6",
            "q_123",
        ] {
            assert_eq!(parse_pointer(bad), None, "{bad}");
        }
    }

    #[test]
    fn check_reports_bad_fields_missing_and_self_targets() {
        let p = plan(&format!(
            r#"{{"id":"A","after":["node:B","node:Z","node:A","pr:x"],{OK}}},
               {{"id":"B","approved":{{"at":"","by":"Bob","pointer":"說可以"}}}}"#
        ));
        let got = check(&p, None);
        assert!(
            got.contains(&Problem::NoSuchNode {
                node: "A".into(),
                target: "Z".into()
            }),
            "{got:?}"
        );
        assert!(got.contains(&Problem::SelfRef { node: "A".into() }));
        assert!(got.contains(&Problem::BadAfter {
            node: "A".into(),
            item: "pr:x".into()
        }));
        assert!(got.contains(&Problem::ApprovedMissing {
            node: "B".into(),
            field: "at"
        }));
        assert!(got.contains(&Problem::ApprovedBy {
            node: "B".into(),
            by: "Bob".into()
        }));
        assert!(got.contains(&Problem::BadPointer {
            node: "B".into(),
            pointer: "說可以".into()
        }));
        assert_eq!(got.len(), 6, "{got:?}");
    }

    #[test]
    fn check_finds_two_and_three_node_cycles_once_each() {
        let p = plan(
            r#"{"id":"A","after":["node:B"]},{"id":"B","after":["node:A"]},
               {"id":"C","after":["node:D"]},{"id":"D","after":["node:E"]},{"id":"E","after":["node:C"]},
               {"id":"F","after":["node:A"]}"#,
        );
        let cyc: Vec<_> = check(&p, None)
            .into_iter()
            .filter_map(|x| match x {
                Problem::Cycle { nodes } => Some(nodes),
                _ => None,
            })
            .collect();
        assert_eq!(
            cyc,
            vec![
                vec!["A".to_string(), "B".into()],
                vec!["C".into(), "D".into(), "E".into()]
            ]
        );
    }

    #[test]
    fn a_pointer_to_a_pending_file_that_is_gone_is_reported_only_when_pending_was_read() {
        let p = plan(&format!(r#"{{"id":"A",{OK}}}"#));
        assert!(check(&p, None).is_empty());
        let have: HashSet<String> = ["DEV-S79-drive-next-519d0-1114.md".to_string()].into();
        assert!(check(&p, Some(&have)).is_empty());
        let other: HashSet<String> = ["x.md".to_string()].into();
        assert_eq!(
            check(&p, Some(&other)),
            vec![Problem::PointerGone {
                node: "A".into(),
                file: "DEV-S79-drive-next-519d0-1114.md".into()
            }]
        );
    }

    fn src(prs: &[(u32, PrState)]) -> Sources {
        Sources {
            prs: prs.iter().copied().collect(),
            ..Sources::default()
        }
    }

    #[test]
    fn stuck_goes_approval_then_before_then_claim_then_unknown_then_ready() {
        let p = plan(&format!(
            r#"{{"id":"P","status":"實查通過"}},{{"id":"Q","status":"做中"}},
               {{"id":"none"}},
               {{"id":"wait","after":["node:P"]}},
               {{"id":"before","after":["node:P","node:Q","pr:1"],{OK}}},
               {{"id":"unk","after":["node:P","pr:2"],{OK}}},
               {{"id":"ready","after":["node:P","pr:1"],{OK}}},
               {{"id":"closed","after":["pr:3"],{OK}}}"#
        ));
        let ix = index(&p);
        let s = src(&[(1, PrState::Merged), (3, PrState::Closed)]);
        let at = |id: &str| stuck(&p, &ix, ix.by[id], &s);
        assert_eq!(at("none"), None, "no field: nothing shown");
        assert_eq!(at("wait"), Some(Stuck::NotApproved));
        assert_eq!(at("before"), Some(Stuck::Before(vec!["node:Q".into()])));
        assert_eq!(
            at("unk"),
            Some(Stuck::Unknown(vec!["pr:2".into()])),
            "unread is not ready"
        );
        assert_eq!(at("ready"), Some(Stuck::Ready));
        assert_eq!(
            at("closed"),
            Some(Stuck::Before(vec!["pr:3".into()])),
            "closed unmerged is not done"
        );
        // claim: a source that exists blocks; no source (S73h not live) does not
        let mut c = s.clone();
        c.claims = Some([("ready".to_string(), "a9136".to_string())].into());
        assert_eq!(
            stuck(&p, &ix, ix.by["ready"], &c),
            Some(Stuck::Claimed("a9136".into()))
        );
        assert_eq!(Stuck::Claimed("a9136".into()).short(), "被 a9136 claim");
    }

    #[test]
    fn a_badly_written_approval_is_never_ready() {
        let p = plan(
            r#"{"id":"empty","approved":{}},
               {"id":"by","approved":{"at":"11-14","by":"519d0","pointer":"q_0123456789abcdef"}},
               {"id":"ptr","approved":{"at":"11-14","by":"Andy","pointer":"see chat"}},
               {"id":"gone","approved":{"at":"11-14","by":"Andy","pointer":"DEV-gone.md §1"}},
               {"id":"good","approved":{"at":"11-14","by":"Andy","pointer":"DEV-here.md §1"}}"#,
        );
        let ix = index(&p);
        let s = Sources {
            pending: Some(["DEV-here.md".to_string()].into()),
            ..Sources::default()
        };
        for id in ["empty", "by", "ptr", "gone"] {
            assert_eq!(
                stuck(&p, &ix, ix.by[id], &s),
                Some(Stuck::BadApproval),
                "{id}: a wrong approved field is not an approval"
            );
        }
        assert_eq!(stuck(&p, &ix, ix.by["good"], &s), Some(Stuck::Ready));
        // no pending listing: the file is not checked, so a well-formed pointer passes
        assert_eq!(
            stuck(&p, &ix, ix.by["gone"], &Sources::default()),
            Some(Stuck::Ready)
        );
        assert_eq!(Stuck::BadApproval.short(), "同意欄寫錯");
    }

    #[test]
    fn a_misspelt_or_missing_dependency_is_never_ready() {
        let p = plan(&format!(
            r#"{{"id":"A","after":["node:Nope","pr:x"],{OK}}}"#
        ));
        let ix = index(&p);
        assert_eq!(
            stuck(&p, &ix, 0, &Sources::default()),
            Some(Stuck::Before(vec!["node:Nope".into(), "pr:x".into()]))
        );
    }

    #[test]
    fn wanted_prs_and_gh_state() {
        let p = plan(r#"{"id":"A","after":["pr:5","skills-pr:9","node:A","pr:5"]}"#);
        let (a, s) = wanted_prs(&p);
        assert_eq!(
            (
                a.into_iter().collect::<Vec<_>>(),
                s.into_iter().collect::<Vec<_>>()
            ),
            (vec![5], vec![9])
        );
        assert_eq!(
            parse_pr_state(r#"{"state":"MERGED"}"#),
            Some(PrState::Merged)
        );
        assert_eq!(parse_pr_state(r#"{"state":"DRAFT"}"#), None);
        assert_eq!(parse_pr_state("not json"), None);
    }
}
