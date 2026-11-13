//! 艙進度樹(純邏輯，不碰終端機):讀進度資料、算完成度、依展開與篩選攤平成列、
//! 依寬度切字。畫面與按鍵在 `main.rs`。
//!
//! 完成度規則與網頁版、Claude mod 同一套:每個有狀態的節點算一件，
//! 「實查通過」算完成;上層 = 底下(含自己)完成數 / 總數。

pub mod mainline;
pub mod prs;

use serde::Deserialize;
use std::collections::{BTreeMap, HashMap, HashSet};

pub const PASS: &str = "實查通過";

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Node {
    pub id: String,
    #[serde(default)]
    pub parent: Option<String>,
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub why: Option<String>,
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub owner: Option<String>,
    #[serde(default)]
    pub with: Vec<String>,
    #[serde(default)]
    pub handoff: Option<String>,
    #[serde(default)]
    pub start: Option<String>,
    #[serde(default)]
    pub updated: Option<String>,
    #[serde(default)]
    pub eta: Option<String>,
    #[serde(default)]
    pub evidence: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct DmCount {
    pub waiting: Option<u32>,
    pub read: Option<u32>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Seat {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub cabin: String,
    #[serde(default)]
    pub dm: Option<DmCount>,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct Plan {
    pub nodes: Vec<Node>,
    #[serde(default)]
    pub seats: BTreeMap<String, Seat>,
    #[serde(default)]
    pub asof: String,
    #[serde(default)]
    pub cabin: String,
}

#[derive(Debug, Clone, Default)]
pub struct Filter {
    pub q: String,
    pub seat: String,
    pub only_open: bool,
    pub only_ext: bool,
    pub orphan: bool,
}

impl Filter {
    pub fn active(&self) -> bool {
        !self.q.is_empty()
            || !self.seat.is_empty()
            || self.only_open
            || self.only_ext
            || self.orphan
    }
}

/// 一列：節點在 `Plan::nodes` 的位置與縮排深度。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub idx: usize,
    pub depth: usize,
}

pub struct Index {
    pub by: HashMap<String, usize>,
    pub kids: Vec<Vec<usize>>,
    pub root: Option<usize>,
}

pub fn parse(text: &str) -> Result<Plan, String> {
    serde_json::from_str::<Plan>(text).map_err(|e| format!("bad_plan_json: {e}"))
}

pub fn index(p: &Plan) -> Index {
    let mut by = HashMap::new();
    for (i, n) in p.nodes.iter().enumerate() {
        by.insert(n.id.clone(), i);
    }
    let mut kids = vec![Vec::new(); p.nodes.len()];
    let mut root = None;
    for (i, n) in p.nodes.iter().enumerate() {
        match n.parent.as_ref().and_then(|x| by.get(x)) {
            Some(&pi) if pi != i => kids[pi].push(i),
            _ => {
                if root.is_none() {
                    root = Some(i);
                }
            }
        }
    }
    Index { by, kids, root }
}

/// (完成數, 總數);用顯式堆疊，資料有環也不會無限遞迴。
pub fn tally(p: &Plan, ix: &Index, at: usize) -> (u32, u32) {
    let (mut ok, mut tot) = (0, 0);
    let mut stack = vec![at];
    let mut seen = HashSet::new();
    while let Some(i) = stack.pop() {
        if !seen.insert(i) {
            continue;
        }
        if let Some(s) = &p.nodes[i].status {
            tot += 1;
            if s == PASS {
                ok += 1;
            }
        }
        stack.extend(ix.kids[i].iter().copied());
    }
    (ok, tot)
}

pub fn pct((ok, tot): (u32, u32)) -> u32 {
    (ok * 100 + tot / 2).checked_div(tot).unwrap_or(0)
}

fn matches(n: &Node, f: &Filter) -> bool {
    if !(n.kind == "task" || n.kind == "ext") {
        return false;
    }
    if !f.q.is_empty() {
        let hay = format!(
            "{} {} {} {}",
            n.id,
            n.title,
            n.evidence.as_deref().unwrap_or(""),
            n.owner.as_deref().unwrap_or("")
        )
        .to_lowercase();
        if !hay.contains(&f.q.to_lowercase()) {
            return false;
        }
    }
    if !f.seat.is_empty()
        && n.owner.as_deref() != Some(f.seat.as_str())
        && !n.with.iter().any(|w| w == &f.seat)
    {
        return false;
    }
    if f.only_open && n.status.as_deref() == Some(PASS) {
        return false;
    }
    if f.only_ext && n.kind != "ext" {
        return false;
    }
    if f.orphan && !(n.kind == "task" && n.owner.is_none()) {
        return false;
    }
    true
}

/// 依展開集合與篩選攤平成要畫的列。篩選時命中的節點連同祖先都顯示、全部展開。
pub fn flatten(p: &Plan, ix: &Index, open: &HashSet<String>, f: &Filter) -> Vec<Row> {
    let filtering = f.active();
    let mut vis: HashSet<usize> = HashSet::new();
    if filtering {
        for (i, n) in p.nodes.iter().enumerate() {
            if matches(n, f) {
                let mut cur = Some(i);
                let mut guard = 0;
                while let Some(c) = cur {
                    if !vis.insert(c) || guard > p.nodes.len() {
                        break;
                    }
                    guard += 1;
                    cur = p.nodes[c]
                        .parent
                        .as_ref()
                        .and_then(|x| ix.by.get(x))
                        .copied();
                }
            }
        }
    }
    let mut rows = Vec::new();
    let Some(root) = ix.root else { return rows };
    let mut stack = vec![(root, 0usize)];
    let mut seen = HashSet::new();
    while let Some((i, d)) = stack.pop() {
        if !seen.insert(i) || (filtering && !vis.contains(&i)) {
            continue;
        }
        rows.push(Row { idx: i, depth: d });
        if filtering || open.contains(&p.nodes[i].id) {
            for &k in ix.kids[i].iter().rev() {
                stack.push((k, d + 1));
            }
        }
    }
    rows
}

/// 從根到這個節點的鏈(「為什麼在做這件」)。
pub fn chain(p: &Plan, ix: &Index, at: usize) -> Vec<usize> {
    let mut out = vec![at];
    let mut cur = at;
    while let Some(&pi) = p.nodes[cur].parent.as_ref().and_then(|x| ix.by.get(x)) {
        if out.contains(&pi) {
            break;
        }
        out.push(pi);
        cur = pi;
    }
    out.reverse();
    out
}

/// 終端機顯示寬度：東亞全形與表情算 2 格，其他算 1。
pub fn cell_width(c: char) -> usize {
    let u = c as u32;
    let wide = (0x1100..=0x115F).contains(&u)
        || (0x2E80..=0xA4CF).contains(&u)
        || (0xAC00..=0xD7A3).contains(&u)
        || (0xF900..=0xFAFF).contains(&u)
        || (0xFE30..=0xFE4F).contains(&u)
        || (0xFF00..=0xFF60).contains(&u)
        || (0xFFE0..=0xFFE6).contains(&u)
        || (0x1F300..=0x1FAFF).contains(&u)
        || (0x20000..=0x3FFFD).contains(&u);
    if wide {
        2
    } else {
        1
    }
}

pub fn str_width(s: &str) -> usize {
    s.chars().map(cell_width).sum()
}

/// 切到最多 `w` 格，超過補「…」。
pub fn fit(s: &str, w: usize) -> String {
    if str_width(s) <= w {
        return s.to_string();
    }
    if w == 0 {
        return String::new();
    }
    let mut out = String::new();
    let mut used = 0;
    for c in s.chars() {
        let cw = cell_width(c);
        if used + cw + 1 > w {
            break;
        }
        out.push(c);
        used += cw;
    }
    out.push('…');
    out
}

/// 補空白到剛好 `w` 格(先切)。
pub fn pad(s: &str, w: usize) -> String {
    let t = fit(s, w);
    let n = w.saturating_sub(str_width(&t));
    format!("{t}{}", " ".repeat(n))
}

pub fn bar(p: u32, w: usize) -> String {
    let full = (p as usize * w + 50) / 100;
    format!("{}{}", "█".repeat(full.min(w)), "░".repeat(w - full.min(w)))
}

pub fn mark(
    p: &Plan,
    ix: &Index,
    i: usize,
    open: &HashSet<String>,
    filtering: bool,
) -> &'static str {
    if !ix.kids[i].is_empty() {
        return if filtering || open.contains(&p.nodes[i].id) {
            "▾"
        } else {
            "▸"
        };
    }
    match p.nodes[i].status.as_deref() {
        Some(PASS) => "✓",
        Some("實查不通過") => "✗",
        Some("未驗") => "?",
        _ => "·",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plan() -> Plan {
        parse(
            r#"{"cabin":"9320","asof":"t","seats":{"a1":{"name":"x","cabin":"9320","dm":{"waiting":1,"read":2}}},
            "nodes":[
              {"id":"V","parent":null,"kind":"vision","title":"總大綱"},
              {"id":"G1","parent":"V","kind":"group","title":"大項"},
              {"id":"I1","parent":"G1","kind":"item","title":"項目"},
              {"id":"T1","parent":"I1","kind":"task","title":"做完的","status":"實查通過","owner":"a1"},
              {"id":"T2","parent":"I1","kind":"task","title":"沒人做","status":"未開始"},
              {"id":"X1","parent":"T2","kind":"ext","title":"延伸","status":"未驗","owner":"a1"}
            ]}"#,
        )
        .expect("fixture parses")
    }

    #[test]
    fn tally_counts_only_status_nodes() {
        let p = plan();
        let ix = index(&p);
        assert_eq!(tally(&p, &ix, ix.by["V"]), (1, 3));
        assert_eq!(pct(tally(&p, &ix, ix.by["T2"])), 0);
        assert_eq!(pct((1, 3)), 33);
    }

    #[test]
    fn collapsed_shows_only_root_and_open_levels() {
        let p = plan();
        let ix = index(&p);
        let open: HashSet<String> = ["V".to_string()].into();
        let rows = flatten(&p, &ix, &open, &Filter::default());
        let ids: Vec<&str> = rows.iter().map(|r| p.nodes[r.idx].id.as_str()).collect();
        assert_eq!(ids, vec!["V", "G1"]);
    }

    #[test]
    fn filter_keeps_ancestors_and_drops_passed() {
        let p = plan();
        let ix = index(&p);
        let f = Filter {
            only_open: true,
            ..Filter::default()
        };
        let rows = flatten(&p, &ix, &HashSet::new(), &f);
        let ids: Vec<&str> = rows.iter().map(|r| p.nodes[r.idx].id.as_str()).collect();
        assert_eq!(ids, vec!["V", "G1", "I1", "T2", "X1"]);
    }

    #[test]
    fn orphan_filter_finds_task_without_owner() {
        let p = plan();
        let ix = index(&p);
        let f = Filter {
            orphan: true,
            ..Filter::default()
        };
        let rows = flatten(&p, &ix, &HashSet::new(), &f);
        assert!(rows.iter().any(|r| p.nodes[r.idx].id == "T2"));
        assert!(!rows.iter().any(|r| p.nodes[r.idx].id == "T1"));
    }

    #[test]
    fn chain_runs_root_to_node_and_survives_cycle() {
        let p = plan();
        let ix = index(&p);
        let c: Vec<&str> = chain(&p, &ix, ix.by["X1"])
            .iter()
            .map(|&i| p.nodes[i].id.as_str())
            .collect();
        assert_eq!(c, vec!["V", "G1", "I1", "T2", "X1"]);
        let mut q = plan();
        q.nodes[0].parent = Some("X1".into());
        let iq = index(&q);
        assert!(chain(&q, &iq, iq.by["X1"]).len() <= q.nodes.len());
    }

    #[test]
    fn width_counts_cjk_as_two_and_fit_never_overflows() {
        assert_eq!(str_width("ab中"), 4);
        let s = fit("席位重開也能即時接上", 9);
        assert!(str_width(&s) <= 9, "{s}");
        assert_eq!(str_width(&pad("中文", 7)), 7);
    }

    #[test]
    fn bad_json_is_an_error_not_a_panic() {
        assert!(parse("{").is_err());
    }

    #[test]
    fn fit_zero_width_draws_nothing() {
        assert_eq!(fit("進度樹", 0), "");
        assert_eq!(str_width(&fit("進度樹", 1)), 1);
        assert!(str_width(&fit("進度樹abc", 4)) <= 4);
    }
}
