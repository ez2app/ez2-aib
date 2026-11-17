//! 右邊三站由程式判(S107-judge;DECISION-council-board-worklist-519d0-1117 步 4;Andy 11-17 06:3x
//! 同意過渡一週「寫的站 / 判的站」並列,之後右邊三站由程式說了算)。
//!
//! 每張寫在開發中、審過未推、審過排隊、未驗的卡,拿整份紀錄照 [`card::entry_problems`](`set` 換站用的
//! 同一套條件,SPEC-station-entry-review-5d33a-1117 RV1–RV7 + 進疊 / 進未驗)由右往左試:條件全過的最右
//! 一站就是判的站,都不過 = 開發中。只讀、不寫卡;看板與派工讀同一份輸出(`aib-plan judge --json`)。

use crate::card::{self, Facts, Words};
use crate::{Node, Plan};

/// 右邊三站的位置(STATIONS:3 審過未推、4 審過排隊、5 未驗);開發中 = 2
const DEV: usize = 2;
const RIGHT: [usize; 3] = [5, 4, 3];

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Judged {
    pub id: String,
    pub owner: Option<String>,
    pub written: String,
    pub judged: String,
    pub agree: bool,
    /// 寫的站比判的站右 ⇒ 寫的站缺什麼;否則 ⇒ 判的站再往右一站缺什麼(判在未驗 ⇒ 空)
    pub why: Vec<String>,
}

/// 一張卡;不在開發中到未驗之間(含改名的站詞)⇒ None
pub fn judge_node(n: &Node, f: &dyn Facts, w: &Words) -> Option<Judged> {
    let written = n.status.as_deref()?;
    let (wi, _) = w.station(written)?;
    if !(DEV..=5).contains(&wi) {
        return None;
    }
    let record = n.evidence.as_deref().unwrap_or("");
    let ji = RIGHT
        .into_iter()
        .find(|&k| card::entry_problems(k, record, n, f).is_empty())
        .unwrap_or(DEV);
    let why = if wi > ji {
        card::entry_problems(wi, record, n, f)
    } else if ji < 5 {
        card::entry_problems(ji + 1, record, n, f)
    } else {
        Vec::new()
    };
    Some(Judged {
        id: n.id.clone(),
        owner: n.owner.clone(),
        written: written.to_string(),
        judged: w.name(ji).to_string(),
        agree: wi == ji,
        why,
    })
}

/// 全部卡;`seat` 有值 ⇒ 只列這一席有角色的(作者 / 審 / 驗 / 協作);開發中且判也是開發中的不列(沒話說)
pub fn judge_all(p: &Plan, seat: Option<&str>, f: &dyn Facts, w: &Words) -> Vec<Judged> {
    p.nodes
        .iter()
        .filter(|n| seat.is_none_or(|s| card::role(n, s).is_some()))
        .filter_map(|n| judge_node(n, f, w))
        .filter(|j| !(j.agree && w.station(&j.written).map(|x| x.0) == Some(DEV)))
        .collect()
}

pub fn format_row(j: &Judged) -> String {
    let mark = if j.agree { "同" } else { "不同" };
    let mut s = format!("{}\t寫:{}\t判:{}\t{}", j.id, j.written, j.judged, mark);
    if !j.why.is_empty() {
        s.push_str("\t缺:");
        s.push_str(&j.why.join(";"));
    }
    s
}

/// 一次讀全部開著的 PR(號碼、分支、頭),之後 `pr` / `pr_of_branch` 不再各打一次 gh;
/// 讀不到清單 ⇒ 照舊問 `inner`
pub struct OpenPrs<'a> {
    pub inner: &'a dyn Facts,
    pub open: Option<Vec<(u32, String, String)>>,
}

impl Facts for OpenPrs<'_> {
    fn pending(&self, name: &str) -> Option<String> {
        self.inner.pending(name)
    }
    fn in_main(&self, rev: &str) -> Option<bool> {
        self.inner.in_main(rev)
    }
    fn ancestor(&self, a: &str, b: &str) -> Option<bool> {
        self.inner.ancestor(a, b)
    }
    fn pr(&self, n: u32) -> Option<(bool, String)> {
        match &self.open {
            Some(v) => Some(
                v.iter()
                    .find(|p| p.0 == n)
                    .map_or((false, String::new()), |p| (true, p.2.clone())),
            ),
            None => self.inner.pr(n),
        }
    }
    fn pr_of_branch(&self, branch: &str) -> Option<(bool, String)> {
        match &self.open {
            // 不在開著的清單 ⇒ 交回 inner(那條分支可能有關掉的 PR,說「沒開著」比「查不到」準)
            Some(v) => match v.iter().find(|p| p.1 == branch) {
                Some(p) => Some((true, p.2.clone())),
                None => self.inner.pr_of_branch(branch),
            },
            None => self.inner.pr_of_branch(branch),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const H: &str = "cccccccccccccccccccccccccccccccccccccccc";
    const OLD: &str = "dddddddddddddddddddddddddddddddddddddddd";

    struct F {
        files: Vec<(&'static str, String)>,
        main: Option<bool>,
        open: Vec<(u32, String, String)>,
    }
    impl Facts for F {
        fn pending(&self, name: &str) -> Option<String> {
            self.files.iter().find(|x| x.0 == name).map(|x| x.1.clone())
        }
        fn in_main(&self, _: &str) -> Option<bool> {
            self.main
        }
        fn ancestor(&self, a: &str, b: &str) -> Option<bool> {
            Some(a == b)
        }
        fn pr(&self, n: u32) -> Option<(bool, String)> {
            let p = self.open.iter().find(|p| p.0 == n);
            Some(p.map_or((false, String::new()), |p| (true, p.2.clone())))
        }
        fn pr_of_branch(&self, _: &str) -> Option<(bool, String)> {
            None
        }
    }
    fn green(head: &str) -> String {
        format!("x\nRC fmt=0\nRC test=0\nKNIVES-RC 0\nMISSES-COUNT 0\nHEAD-END {head}\n")
    }
    fn review(head: &str) -> String {
        format!("結論: 通過 頭: {head} H/M/L/N: 0/0/0/1 範圍: 全文\n")
    }
    fn node(status: &str, evidence: &str) -> Node {
        let mut p = crate::parse(
            r#"{"nodes":[{"id":"S9","parent":null,"title":"t","owner":"a9136;審 5d33a"}]}"#,
        )
        .expect("parse");
        let mut n = p.nodes.remove(0);
        n.status = Some(status.into());
        n.evidence = Some(evidence.into());
        n
    }
    fn facts(main: Option<bool>, open: Vec<(u32, String, String)>) -> F {
        F {
            files: vec![
                ("REVIEW-x-5d33a-cccccccc.md", review(H)),
                ("RECEIPT-lin-a9136-r1-ccccccccc.out", green(H)),
                ("RECEIPT-lin-a9136-r0-ddddddddd.out", green(OLD)),
            ],
            main,
            open,
        }
    }
    const REC: &str = "頭 cccccccccccccccccccccccccccccccccccccccc REVIEW-x-5d33a-cccccccc.md RECEIPT-lin-a9136-r1-ccccccccc.out";

    #[test]
    fn a_card_written_ahead_of_its_proof_is_judged_back_and_says_what_is_missing() {
        let w = Words::default();
        // 寫在審過排隊,但紀錄裡沒有 PR ⇒ 判審過未推,缺「#PR 號或 integ/ 分支」
        let j =
            judge_node(&node("審過排隊", REC), &facts(Some(false), vec![]), &w).expect("judged");
        assert_eq!((j.judged.as_str(), j.agree), ("審過未推", false));
        assert!(j.why.iter().any(|x| x.contains("#PR")), "{:?}", j.why);
        // 收據只有舊頭 ⇒ 連審過未推都不到,判開發中
        let rec = format!("頭 {H} REVIEW-x-5d33a-cccccccc.md RECEIPT-lin-a9136-r0-ddddddddd.out");
        let j =
            judge_node(&node("審過未推", &rec), &facts(Some(false), vec![]), &w).expect("judged");
        assert_eq!((j.judged.as_str(), j.agree), ("開發中", false));
        assert!(
            j.why.iter().any(|x| x.contains("RECEIPT-lin-a9136-r0")),
            "{:?}",
            j.why
        );
    }

    #[test]
    fn a_card_behind_its_proof_is_judged_forward_through_the_open_pr_list_and_main() {
        let w = Words::default();
        let rec = format!("{REC} 進疊 #900");
        let open = vec![(900, "integ/x".to_string(), H.to_string())];
        let j = judge_node(&node("開發中", &rec), &facts(Some(false), open), &w).expect("judged");
        assert_eq!((j.judged.as_str(), j.agree), ("審過排隊", false));
        // 下一站(未驗)缺:頭不在 origin/main
        assert!(
            j.why.iter().any(|x| x.contains("origin/main")),
            "{:?}",
            j.why
        );
        // 合進主線了 ⇒ 未驗,沒有缺的
        let j =
            judge_node(&node("審過排隊", &rec), &facts(Some(true), vec![]), &w).expect("judged");
        assert_eq!(
            (j.judged.as_str(), j.agree, j.why.len()),
            ("未驗", false, 0)
        );
        // PR 關了(不在開著的清單)⇒ 不算審過排隊
        let j =
            judge_node(&node("審過排隊", &rec), &facts(Some(false), vec![]), &w).expect("judged");
        assert_eq!(j.judged, "審過未推");
    }

    #[test]
    fn only_the_middle_stations_are_judged_and_quiet_dev_cards_are_left_out() {
        let w = Words::default();
        let f = facts(Some(false), vec![]);
        for s in ["未開始", "已定未做", "實查通過", "作廢"] {
            assert_eq!(judge_node(&node(s, REC), &f, &w), None, "{s}");
        }
        let mut p = crate::parse(r#"{"nodes":[]}"#).expect("parse");
        p.nodes = vec![
            node("開發中", "只開了分支 feat/x-a9136"),
            node("審過未推", REC),
        ];
        p.nodes[0].id = "S8".into();
        let all = judge_all(&p, Some("a9136"), &f, &w);
        assert_eq!(
            all.iter().map(|j| j.id.as_str()).collect::<Vec<_>>(),
            ["S9"]
        );
        assert!(judge_all(&p, Some("043dd"), &f, &w).is_empty());
        assert_eq!(
            format_row(&all[0]),
            "S9\t寫:審過未推\t判:審過未推\t同\t缺:要寫 #PR 號或 integ/ 分支"
        );
    }

    #[test]
    fn the_open_pr_list_answers_for_every_number_and_falls_back_when_missing() {
        // the one-by-one answer differs (older head) so a fallback is told apart from the list
        let inner = facts(None, vec![(7, "integ/a".into(), OLD.into())]);
        let o = OpenPrs {
            inner: &inner,
            open: Some(vec![(7, "integ/a".into(), H.into())]),
        };
        assert_eq!(o.pr(7), Some((true, H.to_string())));
        assert_eq!(o.pr(8), Some((false, String::new())));
        assert_eq!(o.pr_of_branch("integ/a"), Some((true, H.to_string())));
        assert_eq!(o.pr_of_branch("integ/b"), None);
        let o = OpenPrs {
            inner: &inner,
            open: None,
        };
        assert_eq!(o.pr(7), Some((true, OLD.to_string())));
    }
}
