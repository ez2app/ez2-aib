//! `aib-plan migrate`:舊站名轉七站 + 負責席欄混寫拆欄(DECISION-council-gaps-519d0-1117 R1 / R5)
//!
//! 只算、不寫是預設([`rows`] 印對照表給驗收席抽核);`--write` 才逐張經 [`crate::card::Op::Migrate`]
//! 寫,每張一行事件帳(`op: migrate`,帶 from / to 與原負責席欄),要還原照事件帳一張一張寫回。
//! 不為了過進站條件填假的前置或 touches(Codex):看不出來的列「人看」,不自動轉。
//!
//! 對照(R1):已同意未做 → 已定未做;等前置 → 未開始 + 等誰(沒寫等誰 ⇒ 人看);暫停 → 有頭的
//! 開發中、沒頭的已定未做,停因是在途上限 / 重分排序的也進已定未做(5d33a 提醒 S76-close);
//! 驗過 → 實查通過;卡住 → 已定未做;實查不通過 → 未驗 + 驗不通過旗子(不退開發中)。
//! 暫停 / 卡住的原因取紀錄裡最後一段寫「→ 暫停」(或裁定 / 重分 / 原站)的,找不到 ⇒ 人看(5d33a 錯
//! S79-switch)。期望卡(EA…)、goal、母卡(有子卡)不轉(0eed7 提醒 S89)。
//!
//! 拆欄(R5):負責席欄只留作者;標了 審 / 疊 的段進 reviewer、標了 驗 的段進 verifier。沒有作者段、
//! 作者不只一席、跟既有 reviewer 欄不一致、驗的人是作者 ⇒ 人看。

use crate::card::{is_seat, sha40s, Words};
use crate::{Node, Plan};
use std::collections::BTreeMap;

/// 對照表的一列
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct Row {
    pub id: String,
    pub from: String,
    /// 轉到的站名;None = 站不動(只拆欄)或人看
    pub to: Option<String>,
    pub reason: Option<String>,
    pub head: Option<String>,
    pub verify_failed: Option<String>,
    /// 拆欄後的 (owner, reviewer, verifier)
    pub split: Option<Split>,
    /// 有值 = 人看、不自動寫(寫為什麼)
    pub look: Option<String>,
    pub owner_was: Option<String>,
}

/// 逐張改寫檔(TSV:卡 \t 站名|作廢|skip \t 原因);驗收席抽核後寫的
pub fn parse_overrides(text: &str) -> Result<BTreeMap<String, (String, String)>, String> {
    let mut m = BTreeMap::new();
    for (i, l) in text.lines().enumerate() {
        let l = l.trim_end_matches('\r');
        if l.trim().is_empty() || l.starts_with('#') {
            continue;
        }
        let c: Vec<&str> = l.split('\t').collect();
        if c.len() != 3 || c.iter().any(|x| x.trim().is_empty()) {
            return Err(format!(
                "bad_overrides: 第 {} 行要三欄(卡 / 站 / 原因)",
                i + 1
            ));
        }
        m.insert(
            c[0].trim().to_string(),
            (c[1].trim().to_string(), c[2].trim().to_string()),
        );
    }
    Ok(m)
}

const LEGACY: [&str; 6] = ["已同意未做", "等前置", "暫停", "驗過", "卡住", "實查不通過"];

fn reason(evidence: &str, st: &str) -> Option<String> {
    evidence
        .rsplit(" | ")
        .map(str::trim)
        .find(|s| {
            s.contains(&format!("→ {st}"))
                || s.contains(&format!("→{st}"))
                || (s.contains(st) && ["裁定", "重分", "原站"].iter().any(|w| s.contains(w)))
        })
        .map(String::from)
}

/// 拆欄結果 (owner, reviewer, verifier)
pub type Split = (String, Option<String>, Option<String>);

fn split_owner(n: &Node) -> Option<Result<Split, String>> {
    let o = n.owner.as_deref()?;
    let (mut auth, mut rv, mut vf) = (Vec::new(), Vec::new(), Vec::new());
    let mut marked = false;
    for seg in o.split([';', '；', '/', '、', ',', '，']) {
        let seats: Vec<&str> = seg
            .split(|c: char| !c.is_ascii_alphanumeric())
            .filter(|t| is_seat(t))
            .collect();
        if seats.is_empty() {
            continue;
        }
        let bucket = if seg.contains('驗') {
            marked = true;
            &mut vf
        } else if seg.contains('審') || seg.contains('疊') {
            marked = true;
            &mut rv
        } else {
            &mut auth
        };
        for s in seats {
            if !bucket.contains(&s) {
                bucket.push(s);
            }
        }
    }
    if !marked {
        return None;
    }
    Some(match (auth.as_slice(), rv.first(), vf.first()) {
        ([], ..) => Err("負責席欄沒有作者段".into()),
        ([_, _, ..], ..) => Err(format!("作者不只一席({})", auth.join(","))),
        (_, Some(r), _) if n.reviewer.as_deref().is_some_and(|x| x != *r) => Err(format!(
            "reviewer 欄 {} 跟負責席欄的審 {r} 不一致",
            n.reviewer.as_deref().unwrap_or_default()
        )),
        ([a], _, Some(v)) if a == v => Err("驗的人是作者".into()),
        ([a], r, v) => Ok((
            a.to_string(),
            r.map(|x| x.to_string()),
            v.map(|x| x.to_string()),
        )),
    })
}

/// 算對照表(純函式;只列要動的卡)
pub fn rows(plan: &Plan, w: &Words, ov: &BTreeMap<String, (String, String)>) -> Vec<Row> {
    let parents: std::collections::HashSet<&str> = plan
        .nodes
        .iter()
        .filter_map(|n| n.parent.as_deref())
        .collect();
    let mut out = Vec::new();
    for n in &plan.nodes {
        let st = n.status.as_deref().unwrap_or("");
        let legacy = LEGACY.contains(&st) && !w.names.iter().any(|x| x == st);
        let closed = st == w.name(6) || st == w.void || st == crate::PASS;
        let split = if closed { None } else { split_owner(n) };
        if !legacy && split.is_none() && !ov.contains_key(&n.id) {
            continue;
        }
        let ev = n.evidence.as_deref().unwrap_or("");
        let head = n
            .head
            .clone()
            .or_else(|| sha40s(ev).last().map(|h| h.to_string()));
        let mut r = Row {
            id: n.id.clone(),
            from: st.to_string(),
            to: None,
            reason: None,
            head: head.clone(),
            verify_failed: None,
            split: None,
            look: None,
            owner_was: n.owner.clone(),
        };
        match split {
            Some(Ok(s)) => r.split = Some(s),
            Some(Err(e)) => r.look = Some(e),
            None => {}
        }
        if legacy {
            if n.id.starts_with("EA") || n.kind == "goal" {
                continue;
            }
            if parents.contains(n.id.as_str()) {
                r.look = Some("母卡:站由子卡算".into());
            } else {
                let to = match st {
                    "已同意未做" => Some((1, None)),
                    "驗過" => Some((6, None)),
                    "實查不通過" => {
                        r.verify_failed = Some(
                            reason(ev, st).unwrap_or_else(|| "舊站名「實查不通過」轉入".into()),
                        );
                        Some((5, None))
                    }
                    "等前置" if n.after.is_empty() => {
                        r.look = Some("等前置但沒寫等誰".into());
                        None
                    }
                    "等前置" => Some((0, Some(format!("等 {}", n.after.join(","))))),
                    _ => match reason(ev, st) {
                        None => {
                            r.look = Some(format!("{st}的原因在紀錄裡找不到"));
                            None
                        }
                        Some(why) => {
                            let queued = ["上限", "沒排", "在途", "重分", "集中"]
                                .iter()
                                .any(|k| why.contains(k));
                            let pos = if st == "暫停" && head.is_some() && !queued {
                                2
                            } else {
                                1
                            };
                            Some((pos, Some(why)))
                        }
                    },
                };
                if let Some((pos, why)) = to {
                    r.to = Some(w.name(pos).to_string());
                    r.reason = why;
                }
            }
        }
        if let Some((to, why)) = ov.get(&n.id) {
            if to == "skip" {
                continue;
            }
            r.to = Some(to.clone());
            r.reason = Some(why.clone());
            if r.look.as_deref().is_some_and(|l| {
                !l.contains("負責席") && !l.contains("作者") && !l.contains("reviewer")
            }) {
                r.look = None;
            }
        }
        out.push(r);
    }
    out
}

/// 對照表一列 ⇒ 事實一段(寫進卡的紀錄)
pub fn fact(r: &Row) -> String {
    let mut s = format!("migrate(R1/R5,MAP-plan-migrate):原站 {}", r.from);
    if let Some(t) = &r.to {
        s.push_str(&format!(" → {t}"));
    }
    if let Some(why) = &r.reason {
        s.push_str(&format!(";原因 {why}"));
    }
    if r.split.is_some() {
        s.push_str(&format!(
            ";原負責席欄「{}」拆欄",
            r.owner_was.as_deref().unwrap_or("")
        ));
    }
    s
}

/// 對照表 ⇒ TSV(給人看 / 抽核)
pub fn tsv(rows: &[Row]) -> String {
    let mut s = String::from("卡\t現在\t轉到\t原因\t頭\t拆欄\t人看\n");
    for r in rows {
        let split = r.split.as_ref().map_or(String::new(), |(o, rv, vf)| {
            format!(
                "owner={o} reviewer={} verifier={}",
                rv.as_deref().unwrap_or("-"),
                vf.as_deref().unwrap_or("-")
            )
        });
        let cells = [
            r.id.as_str(),
            r.from.as_str(),
            r.to.as_deref().unwrap_or("-"),
            r.reason.as_deref().unwrap_or(""),
            r.head.as_deref().map_or("-", |h| h.get(..8).unwrap_or(h)),
            split.as_str(),
            r.look.as_deref().unwrap_or(""),
        ];
        let line: Vec<String> = cells.iter().map(|c| c.replace(['\t', '\n'], " ")).collect();
        s.push_str(&line.join("\t"));
        s.push('\n');
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::card::{apply, Op, RealFacts};

    const H: &str = "abababababababababababababababababababab";

    fn plan() -> String {
        let n = |id: &str, parent: &str, status: &str, owner: &str, ev: &str, extra: &str| {
            format!(
                "  {{\n   \"id\": \"{id}\",\n   \"parent\": {parent},\n   \"title\": \"t\",\n   \"status\": \"{status}\",\n   \"owner\": \"{owner}\",\n   \"evidence\": \"{ev}\"{extra}\n  }}"
            )
        };
        let nodes = [
            "  {\n   \"id\": \"G\",\n   \"parent\": null,\n   \"kind\": \"goal\",\n   \"title\": \"g\"\n  }".to_string(),
            n("A1", "\"G\"", "已同意未做", "a9136", "11-16 開卡", ""),
            n("W1", "\"G\"", "等前置", "a9136", "x", ",\n   \"after\": [\n    \"node:A1\"\n   ]"),
            n("W2", "\"G\"", "等前置", "a9136", "x", ""),
            n("P1", "\"G\"", "暫停", "a9136", &format!("頭 {H} | 11-17 12:28 a9136 → 暫停:等 Andy 選字 | 11-17 23:51 a9136 開關條件"), ""),
            n("P2", "\"G\"", "暫停", "a9136", &format!("頭 {H} | 11-17 12:28 a9136 重分暫停(519d0 裁定;原站 開發中)"), ""),
            n("P3", "\"G\"", "暫停", "a9136", "11-17 只寫了別的", ""),
            n("F1", "\"G\"", "實查不通過", "a9136", "11-17 0eed7 → 實查不通過:M1 快取鍵", ""),
            n("V1", "\"G\"", "驗過", "a9136", "x", ""),
            n("M1", "\"G\"", "暫停", "a9136", "→ 暫停:母卡", ""),
            n("M1-1", "\"M1\"", "開發中", "a9136", "x", ""),
            n("O1", "\"G\"", "開發中", "5d33a(驗)/ a9136(修);審 0eed7", "x", ""),
            n("O2", "\"G\"", "開發中", "5d33a(驗)", "x", ""),
            n("O3", "\"G\"", "實查通過", "5d33a(驗)/ a9136", "x", ""),
            n("EA1", "\"G\"", "暫停", "a9136", "x", ""),
        ];
        format!(
            "{{\n \"seats\": {{}},\n \"nodes\": [\n{}\n ],\n \"asof\": \"11-18\"\n}}",
            nodes.join(",\n")
        )
    }

    fn by_id(rows: &[Row], id: &str) -> Option<Row> {
        rows.iter().find(|r| r.id == id).cloned()
    }

    #[test]
    fn old_station_names_map_to_the_seven_stations_the_council_agreed() {
        let p = crate::parse(&plan()).unwrap();
        let rows = rows(&p, &Words::default(), &BTreeMap::new());
        let to = |id: &str| by_id(&rows, id).and_then(|r| r.to);
        assert_eq!(to("A1").as_deref(), Some("已定未做"));
        assert_eq!(to("W1").as_deref(), Some("未開始"));
        assert_eq!(
            by_id(&rows, "W1").unwrap().reason.as_deref(),
            Some("等 node:A1")
        );
        assert!(
            by_id(&rows, "W2").unwrap().look.is_some(),
            "等前置沒寫等誰 ⇒ 人看"
        );
        assert_eq!(to("V1").as_deref(), Some("實查通過"));
        let f = by_id(&rows, "F1").unwrap();
        assert_eq!(f.to.as_deref(), Some("未驗"));
        assert!(f.verify_failed.unwrap().contains("M1 快取鍵"));
        assert!(by_id(&rows, "EA1").is_none(), "期望卡不轉");
        assert!(by_id(&rows, "M1").unwrap().look.unwrap().contains("母卡"));
    }

    /// 5d33a 抽核:原因取「→ 暫停」那段,不是最後一段;停因是重分排序的有頭暫停不算在途
    #[test]
    fn a_paused_card_takes_the_reason_it_was_paused_for() {
        let p = crate::parse(&plan()).unwrap();
        let rows = rows(&p, &Words::default(), &BTreeMap::new());
        let p1 = by_id(&rows, "P1").unwrap();
        assert_eq!(p1.to.as_deref(), Some("開發中"));
        assert!(p1.reason.unwrap().contains("等 Andy 選字"));
        let p2 = by_id(&rows, "P2").unwrap();
        assert_eq!(p2.to.as_deref(), Some("已定未做"), "重分暫停有頭也不算在途");
        assert!(by_id(&rows, "P3").unwrap().look.unwrap().contains("找不到"));
    }

    #[test]
    fn a_mixed_owner_is_split_into_author_reviewer_and_verifier() {
        let p = crate::parse(&plan()).unwrap();
        let rows = rows(&p, &Words::default(), &BTreeMap::new());
        let o1 = by_id(&rows, "O1").unwrap();
        assert_eq!(
            o1.split,
            Some(("a9136".into(), Some("0eed7".into()), Some("5d33a".into())))
        );
        assert!(by_id(&rows, "O2")
            .unwrap()
            .look
            .unwrap()
            .contains("沒有作者段"));
        assert!(by_id(&rows, "O3").is_none(), "實查通過的卡不拆");
    }

    #[test]
    fn an_override_replaces_the_computed_row_and_skip_drops_it() {
        let p = crate::parse(&plan()).unwrap();
        let ov = parse_overrides("P3\t作廢\t併入 S73-ready 片 2\nA1\tskip\t不轉\n").unwrap();
        let rows = rows(&p, &Words::default(), &ov);
        let p3 = by_id(&rows, "P3").unwrap();
        assert_eq!(p3.to.as_deref(), Some("作廢"));
        assert!(p3.look.is_none());
        assert!(by_id(&rows, "A1").is_none());
        assert!(parse_overrides("P3\t作廢\n").is_err());
    }

    #[test]
    fn migrate_writes_only_cards_off_the_seven_stations() {
        let text = plan();
        let p = crate::parse(&text).unwrap();
        let rows = rows(&p, &Words::default(), &BTreeMap::new());
        let f = RealFacts {
            pending: None,
            repo: None,
        };
        let r = by_id(&rows, "F1").unwrap();
        let op = |id: &str, to: Option<&str>| Op::Migrate {
            id: id.into(),
            to: to.map(String::from),
            fact: fact(&r),
            split: None,
            verify_failed: r.verify_failed.clone(),
        };
        let a = apply(&text, &op("F1", Some("未驗")), "a9136", 0, &f).unwrap();
        assert_eq!(a.from.as_deref(), Some("實查不通過"));
        assert_eq!(a.to.as_deref(), Some("未驗"));
        let n = crate::parse(&a.text).unwrap();
        let f1 = n.nodes.iter().find(|n| n.id == "F1").unwrap();
        assert_eq!(f1.status.as_deref(), Some("未驗"));
        assert!(f1.verify_failed.is_some());
        let e = apply(&text, &op("M1-1", Some("未驗")), "a9136", 0, &f).unwrap_err();
        assert!(e.contains("已在七站"), "{e}");
        let o1 = by_id(&rows, "O1").unwrap();
        let a = apply(
            &text,
            &Op::Migrate {
                id: "O1".into(),
                to: None,
                fact: fact(&o1),
                split: o1.split.clone(),
                verify_failed: None,
            },
            "a9136",
            0,
            &f,
        )
        .unwrap();
        let n = crate::parse(&a.text).unwrap();
        let o = n.nodes.iter().find(|n| n.id == "O1").unwrap();
        assert_eq!(o.owner.as_deref(), Some("a9136"));
        assert_eq!(o.reviewer.as_deref(), Some("0eed7"));
        assert_eq!(o.verifier.as_deref(), Some("5d33a"));
        assert!(o.evidence.as_deref().unwrap().contains("原負責席欄"));
        // 5d33a N1:寫進事件帳的那一行帶原負責席欄
        let d = tempfile::tempdir().unwrap();
        let data = d.path().join("data.json");
        std::fs::write(&data, &text).unwrap();
        crate::card::write(
            &data,
            &Op::Migrate {
                id: "O1".into(),
                to: None,
                fact: fact(&o1),
                split: o1.split.clone(),
                verify_failed: None,
            },
            "a9136",
            0,
            &f,
        )
        .unwrap();
        let log = std::fs::read_to_string(d.path().join("plan-events.jsonl")).unwrap();
        let v: serde_json::Value = serde_json::from_str(log.lines().last().unwrap()).unwrap();
        assert_eq!(v["op"], "migrate");
        assert_eq!(v["owner_was"], "5d33a(驗)/ a9136(修);審 0eed7");
        let e = apply(
            &text,
            &Op::Migrate {
                id: "A1".into(),
                to: None,
                fact: "x".into(),
                split: Some(("a9136".into(), None, None)),
                verify_failed: None,
            },
            "a9136",
            0,
            &f,
        )
        .unwrap_err();
        assert!(e.contains("不拆"), "{e}");
    }
}
