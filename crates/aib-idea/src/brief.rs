//! `aib-idea brief --node <S>` (S76c, DEV-S76 §10; Andy 11-14 10:56): one project's whole
//! outline in one pack, for the seat AI to read before it suggests anything, so suggestions
//! stop repeating, fragmenting, or fighting what is already decided.
//!
//! The tool only gathers and prints; it never writes suggestion text (DEV-S76 §10 不做).
//! Pure: the plan, the documents and the ledger are handed in as text; `main.rs` reads files.
//!
//! What it prints:
//! - the plan tree around the node: its chain up to the root, the node itself, its children
//!   and its siblings (id, status, owner, title, evidence);
//! - every document the node's evidence names (`DEV-…md` style names, read from the pending
//!   folder): its section headings and its 「不做」 lines;
//! - measured numbers: this first version reads none and says 「沒數字」, never a guess;
//! - earlier suggestions on the same node, the refused ones with their reason;
//! - the four directions to look at (穩定 / 快 / 簡單 / 沒想到).

use crate::{short, HumanReview, Ledger};
use serde_json::Value;

/// At most this many siblings are listed (the rest are counted).
pub const MAX_SIBLINGS: usize = 20;
/// At most this many lines are taken from one document.
pub const MAX_DOC_LINES: usize = 40;

/// One plan node, as the cabin plan (`cabin-plan/data.json`, read by aib-plan) writes it.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Node {
    pub id: String,
    pub parent: Option<String>,
    pub title: String,
    pub status: Option<String>,
    pub owner: Option<String>,
    pub evidence: Option<String>,
}

/// The nodes of a plan file, or why it cannot be read.
pub fn parse_plan(text: &str) -> Result<Vec<Node>, String> {
    let v: Value = serde_json::from_str(text).map_err(|e| format!("bad plan json: {e}"))?;
    let nodes = v
        .get("nodes")
        .and_then(Value::as_array)
        .ok_or("the plan has no nodes list")?;
    let text = |n: &Value, k: &str| {
        n.get(k)
            .and_then(Value::as_str)
            .filter(|s| !s.trim().is_empty())
            .map(crate::clean)
    };
    Ok(nodes
        .iter()
        .filter_map(|n| {
            Some(Node {
                id: text(n, "id")?,
                parent: text(n, "parent"),
                title: text(n, "title").unwrap_or_default(),
                status: text(n, "status"),
                owner: text(n, "owner"),
                evidence: text(n, "evidence"),
            })
        })
        .collect())
}

/// The node, its chain up (root first, the node last), its children and its siblings.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Around<'a> {
    pub node: &'a Node,
    pub chain: Vec<&'a Node>,
    pub children: Vec<&'a Node>,
    pub siblings: Vec<&'a Node>,
}

pub fn around<'a>(nodes: &'a [Node], id: &str) -> Option<Around<'a>> {
    let find = |id: &str| nodes.iter().find(|n| n.id == id);
    let node = find(id)?;
    let mut chain = vec![node];
    let mut cur = node;
    while let Some(p) = cur.parent.as_deref().and_then(find) {
        if chain.iter().any(|c| c.id == p.id) {
            break; // a loop in the data: stop, do not spin
        }
        chain.push(p);
        cur = p;
    }
    chain.reverse();
    let children = nodes
        .iter()
        .filter(|n| n.parent.as_deref() == Some(id) && n.id != id)
        .collect();
    let siblings = match node.parent.as_deref() {
        Some(p) => nodes
            .iter()
            .filter(|n| n.parent.as_deref() == Some(p) && n.id != id)
            .collect(),
        None => Vec::new(),
    };
    Some(Around {
        node,
        chain,
        children,
        siblings,
    })
}

/// The document names in an evidence text: a word that starts with an upper-case letter,
/// has a `-` in it and ends in `.md`, with no folder in it (so only the pending folder is
/// read). In order, each once.
pub fn doc_names(evidence: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for w in evidence.split(|c: char| {
        c.is_whitespace()
            || "、,\u{ff0c};\u{ff1b}:\u{ff1a}()\u{ff08}\u{ff09}「」[]【】<>《》'\"`".contains(c)
    }) {
        let w = w.trim_end_matches(['.', '。']);
        let ok = w.len() > 3
            && w.ends_with(".md")
            && w.starts_with(|c: char| c.is_ascii_uppercase())
            && w.contains('-')
            && !w.contains(['/', '\\'])
            && !w.contains("..")
            && w.bytes().all(|b| b.is_ascii_graphic());
        if ok && !out.iter().any(|o| o == w) {
            out.push(w.to_owned());
        }
    }
    out
}

/// A document's outline: its `#` headings and every line that says 「不做」, in order, at
/// most [`MAX_DOC_LINES`] (the rest are counted). Fenced code is skipped.
pub fn outline(text: &str) -> (Vec<String>, usize) {
    let mut keep = Vec::new();
    let mut fenced = false;
    for line in text.lines() {
        let t = line.trim();
        if t.starts_with("```") {
            fenced = !fenced;
            continue;
        }
        if fenced {
            continue;
        }
        if t.starts_with('#') || t.contains("不做") {
            keep.push(crate::clean(t).chars().take(200).collect::<String>());
        }
    }
    let more = keep.len().saturating_sub(MAX_DOC_LINES);
    keep.truncate(MAX_DOC_LINES);
    (keep, more)
}

fn node_line(n: &Node) -> String {
    format!(
        "{} · {} · {} · {}",
        n.id,
        n.status.as_deref().unwrap_or("(無狀態)"),
        n.owner.as_deref().unwrap_or("-"),
        n.title.chars().take(80).collect::<String>()
    )
}

/// What [`render`] is handed for each document the evidence names.
pub enum Doc {
    Text(String),
    /// Why it could not be read.
    Unread(String),
}

/// The whole brief as text.
pub fn render(a: &Around<'_>, docs: &[(String, Doc)], ledger: &Ledger) -> String {
    let mut o = String::new();
    let mut put = |s: String| {
        o.push_str(&s);
        o.push('\n');
    };
    put(format!("# 大綱 {} · {}", a.node.id, a.node.title));
    put(String::new());
    put("## 進度樹".into());
    put(format!(
        "- 上層:{}",
        a.chain
            .iter()
            .map(|n| format!("{}({})", n.id, n.status.as_deref().unwrap_or("-")))
            .collect::<Vec<_>>()
            .join(" > ")
    ));
    put(format!("- 本節點:{}", node_line(a.node)));
    put(format!(
        "  證據:{}",
        a.node.evidence.as_deref().unwrap_or("(沒寫)")
    ));
    put(format!("- 子節點 {} 個", a.children.len()));
    for c in &a.children {
        put(format!("  - {}", node_line(c)));
    }
    put(format!("- 兄弟節點 {} 個", a.siblings.len()));
    for s in a.siblings.iter().take(MAX_SIBLINGS) {
        put(format!("  - {}", node_line(s)));
    }
    if a.siblings.len() > MAX_SIBLINGS {
        put(format!("  - …還有 {} 個", a.siblings.len() - MAX_SIBLINGS));
    }
    put(String::new());
    put("## 開發檔(證據裡提到的檔:章節標題與「不做」)".into());
    if docs.is_empty() {
        put("- 證據沒有提到檔".into());
    }
    for (name, d) in docs {
        match d {
            Doc::Unread(why) => put(format!("- {name}:讀不到({why})")),
            Doc::Text(t) => {
                let (lines, more) = outline(t);
                put(format!("- {name}"));
                for l in lines {
                    put(format!("  {l}"));
                }
                if more > 0 {
                    put(format!("  …還有 {more} 行"));
                }
            }
        }
    }
    put(String::new());
    put("## 實測數字".into());
    put("- 沒數字(這一版不讀租約 / 看板 / 合併隊紀錄;要數字自己量,寫進建議的根據)".into());
    put(String::new());
    put(format!("## 以前的建議(--node {})", a.node.id));
    let mine: Vec<_> = ledger
        .ideas
        .iter()
        .filter(|i| i.node.as_deref() == Some(a.node.id.as_str()))
        .collect();
    if mine.is_empty() {
        put("- 無".into());
    }
    for i in mine {
        let verdict = match (i.human_review(), &i.review) {
            (HumanReview::No, Some(r)) => {
                format!("已否決:{}", r.why.as_deref().unwrap_or("(沒寫理由)"))
            }
            (HumanReview::Ok, _) => "已同意".into(),
            _ => "未審".into(),
        };
        put(format!(
            "- {} {} · {} · {}",
            short(&i.id),
            i.label(),
            verdict,
            i.title
        ));
    }
    if !ledger.complete() {
        put("- (帳本不完整,上面可能少列)".into());
    }
    put(String::new());
    put("## 提建議前".into());
    put("- 四個方向各看一次,沒有就寫「無」:穩定 / 快 / 簡單 / 沒想到。".into());
    put(format!(
        "- 每條帶根據(檔:段 或 數字)、--node {} 與 --axis;上面「以前的建議」有的不要重提,被否決的看理由。",
        a.node.id
    ));
    o
}

#[cfg(test)]
mod tests {
    use super::*;

    const PLAN: &str = r#"{"asof":"x","nodes":[
        {"id":"ROOT","title":"根"},
        {"id":"G2","parent":"ROOT","title":"目標","status":"開發中"},
        {"id":"S78","parent":"G2","title":"審修閘","status":"開發中","owner":"043dd",
         "evidence":"規格 DEV-S78-review-gate-519d0-1114.md §2;另見 ../x/EVIL-a.md、Docs/DEV-b.md、REVIEW-a.md、DEV-S78-review-gate-519d0-1114.md"},
        {"id":"S78a","parent":"S78","title":"子","status":"實查通過"},
        {"id":"S97","parent":"G2","title":"兄弟","status":"審過未推"},
        {"id":"LOOP1","parent":"LOOP2","title":"環"},
        {"id":"LOOP2","parent":"LOOP1","title":"環"},
        {"title":"沒有 id 的不算"}
    ]}"#;

    #[test]
    fn the_node_comes_with_its_chain_children_and_siblings() {
        let nodes = parse_plan(PLAN).expect("plan");
        assert_eq!(nodes.len(), 7);
        let a = around(&nodes, "S78").expect("S78");
        let ids = |v: &[&Node]| v.iter().map(|n| n.id.clone()).collect::<Vec<_>>();
        assert_eq!(ids(&a.chain), vec!["ROOT", "G2", "S78"]);
        assert_eq!(ids(&a.children), vec!["S78a"]);
        assert_eq!(ids(&a.siblings), vec!["S97"]);
        assert!(around(&nodes, "NOPE").is_none());
        // a loop in the data ends instead of spinning
        assert_eq!(
            ids(&around(&nodes, "LOOP1").expect("loop").chain),
            vec!["LOOP2", "LOOP1"]
        );
    }

    #[test]
    fn only_plain_document_names_in_the_pending_folder_are_read() {
        let e = parse_plan(PLAN).expect("plan")[2]
            .evidence
            .clone()
            .unwrap_or_default();
        assert_eq!(
            doc_names(&e),
            vec!["DEV-S78-review-gate-519d0-1114.md", "REVIEW-a.md"],
            "a name with a folder in it is never read; each name once"
        );
        assert!(doc_names("readme.md notes.md DEV-a.mdx").is_empty());
    }

    #[test]
    fn the_outline_is_headings_and_the_not_doing_lines() {
        let doc = "# 標題\n內文\n## 1. 做法\n- 一\n**不做**:不讓工具寫建議\n```\n# 不是標題\n```\n## 2. 下一段\n";
        let (lines, more) = outline(doc);
        assert_eq!(
            lines,
            vec![
                "# 標題",
                "## 1. 做法",
                "**不做**:不讓工具寫建議",
                "## 2. 下一段"
            ]
        );
        assert_eq!(more, 0);
        let long: String = (0..50).map(|i| format!("## {i}\n")).collect();
        let (lines, more) = outline(&long);
        assert_eq!((lines.len(), more), (MAX_DOC_LINES, 10));
    }

    #[test]
    fn the_brief_lists_earlier_suggestions_on_the_node_and_says_when_there_are_no_numbers() {
        let nodes = parse_plan(PLAN).expect("plan");
        let a = around(&nodes, "S78").expect("S78");
        let docs = vec![
            (
                "DEV-S78-review-gate-519d0-1114.md".to_owned(),
                Doc::Text("## 9. 分片\n".into()),
            ),
            ("REVIEW-a.md".to_owned(), Doc::Unread("not found".into())),
        ];
        let text = render(&a, &docs, &Ledger::default());
        for want in [
            "# 大綱 S78 · 審修閘",
            "- 上層:ROOT(-) > G2(開發中) > S78(開發中)",
            "- 本節點:S78 · 開發中 · 043dd · 審修閘",
            "  - S78a · 實查通過 · - · 子",
            "  - S97 · 審過未推 · - · 兄弟",
            "- DEV-S78-review-gate-519d0-1114.md\n  ## 9. 分片",
            "- REVIEW-a.md:讀不到(not found)",
            "- 沒數字",
            "## 以前的建議(--node S78)\n- 無",
            "穩定 / 快 / 簡單 / 沒想到",
        ] {
            assert!(text.contains(want), "missing {want:?} in\n{text}");
        }
    }

    fn idea(id: &str, node: Option<&str>, review: Option<(bool, Option<&str>)>) -> crate::Idea {
        crate::Idea {
            id: id.into(),
            at_ms: 0,
            seat: "043dd".into(),
            title: format!("題 {id}"),
            now: String::new(),
            suggest: String::new(),
            benefit: String::new(),
            cost: String::new(),
            touches: vec!["none".into()],
            class: crate::Class::Auto,
            grade: "B".into(),
            axis: "stable".into(),
            node: node.map(Into::into),
            source: None,
            review: review.map(|(ok, why)| crate::Review {
                ok,
                by: "andy".into(),
                quote: "q".into(),
                why: why.map(Into::into),
                at_ms: 0,
                seat: "519d0".into(),
            }),
        }
    }

    #[test]
    fn earlier_suggestions_on_the_node_come_with_the_verdict_and_the_refusal_reason() {
        let nodes = parse_plan(PLAN).expect("plan");
        let a = around(&nodes, "S78").expect("S78");
        let l = Ledger {
            ideas: vec![
                idea(
                    "i_aaaaaaaa1111",
                    Some("S78"),
                    Some((false, Some("跟 §3 衝突"))),
                ),
                idea("i_bbbbbbbb2222", Some("S78"), Some((true, None))),
                idea("i_cccccccc3333", Some("S78"), None),
                idea("i_dddddddd4444", Some("S97"), None),
                idea("i_eeeeeeee5555", None, None),
            ],
            ..Ledger::default()
        };
        let text = render(&a, &[], &l);
        let section = text
            .split("## 以前的建議(--node S78)\n")
            .nth(1)
            .and_then(|s| s.split("\n\n").next())
            .expect("section");
        let lines: Vec<&str> = section.lines().collect();
        assert_eq!(lines.len(), 3, "only this node's suggestions:\n{section}");
        assert!(lines[0].contains("已否決:跟 §3 衝突") && lines[0].contains("題 i_aaaaaaaa1111"));
        assert!(lines[1].contains("已同意"));
        assert!(lines[2].contains("未審"));
        assert!(!text.contains("(帳本不完整"));
        let broken = Ledger {
            errors: vec!["line 3: bad".into()],
            ..Ledger::default()
        };
        assert!(render(&a, &[], &broken).contains("(帳本不完整,上面可能少列)"));
    }
}
