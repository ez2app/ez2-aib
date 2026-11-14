// SPDX-License-Identifier: Apache-2.0
//! 自動審修紀錄(Andy 11-13 01:3x:「開發完一個功能、要自動 review 修 bug 1 次，要列出找出嚴重 / 中度 /
//! 輕度 bug,這要自動至少 1 輪，如果沒有嚴重、中度 bug 那就可以先跳過但註記還有那些問題，完成主線開發，
//! 而每修一個就要在影子續跑下方顯示」)。
//!
//! 每一席做完一個功能跑一輪審查，每個發現寫一列到 `autofix.jsonl`(`aib-fixlog add`):
//! `{"at":<epoch 秒>,"seat":"a63ab","work":"#731 …","sev":"H|M|L","what":"…","state":"fixed|noted","commit":"…"}`
//! - 嚴重(H)/ 中度(M)要修完才算完成 ⇒ `fixed` 帶修正的 commit;
//! - 輕度(L)可以先跳過 ⇒ `noted`,畫面照列，提醒還有哪些問題。
//!
//! 讀的時候壞列、欄位不對的列都跳過(別席寫壞一列不能讓整個分支消失)。

use std::path::Path;

/// 嚴重度：嚴重 / 中度 / 輕度
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Sev {
    High,
    Mid,
    Low,
}

impl Sev {
    pub fn parse(s: &str) -> Option<Sev> {
        match s {
            "H" => Some(Sev::High),
            "M" => Some(Sev::Mid),
            "L" => Some(Sev::Low),
            _ => None,
        }
    }
    pub fn code(self) -> &'static str {
        match self {
            Sev::High => "H",
            Sev::Mid => "M",
            Sev::Low => "L",
        }
    }
    pub fn label(self) -> &'static str {
        match self {
            Sev::High => "嚴重",
            Sev::Mid => "中度",
            Sev::Low => "輕度",
        }
    }
}

/// 一個審查發現
#[derive(Debug, Clone, PartialEq)]
pub struct Fix {
    /// epoch 秒
    pub at: i64,
    pub seat: String,
    /// 哪個功能(PR 號 / 分支 / 一句話)
    pub work: String,
    pub sev: Sev,
    /// 問題是什麼
    pub what: String,
    /// true = 已修;false = 先記下沒修(只准輕度)
    pub fixed: bool,
    pub commit: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Fixes {
    /// 新的在前
    pub items: Vec<Fix>,
}

impl Fixes {
    pub fn fixed(&self) -> usize {
        self.items.iter().filter(|f| f.fixed).count()
    }
    pub fn noted(&self) -> usize {
        self.items.len() - self.fixed()
    }
}

/// 一列 ⇒ 一個發現;任何一格不對 ⇒ None
pub fn parse_line(line: &str) -> Option<Fix> {
    let v: serde_json::Value = serde_json::from_str(line).ok()?;
    let s = |k: &str| {
        v.get(k)
            .and_then(|x| x.as_str())
            .map(str::trim)
            .filter(|x| !x.is_empty())
    };
    let sev = Sev::parse(s("sev")?)?;
    let fixed = match s("state")? {
        "fixed" => true,
        "noted" => false,
        _ => return None,
    };
    // 嚴重 / 中度不能只記不修
    if !fixed && sev != Sev::Low {
        return None;
    }
    Some(Fix {
        at: v.get("at")?.as_i64().filter(|t| *t > 0)?,
        seat: s("seat")?.to_string(),
        work: s("work")?.to_string(),
        sev,
        what: s("what")?.to_string(),
        fixed,
        commit: s("commit").map(str::to_string),
    })
}

pub fn parse(text: &str) -> Fixes {
    let mut items: Vec<Fix> = text.lines().filter_map(parse_line).collect();
    items.sort_by_key(|f| std::cmp::Reverse(f.at));
    Fixes { items }
}

/// 讀不到檔 ⇒ None(分支不顯示)
pub fn read(path: &Path) -> Option<Fixes> {
    std::fs::read_to_string(path).ok().map(|t| parse(&t))
}

/// 寫一列用的 JSON(不含換行);跟 `parse_line` 互為反函數
pub fn to_line(f: &Fix) -> String {
    let mut m = serde_json::Map::new();
    m.insert("at".into(), f.at.into());
    m.insert("seat".into(), f.seat.clone().into());
    m.insert("work".into(), f.work.clone().into());
    m.insert("sev".into(), f.sev.code().into());
    m.insert("what".into(), f.what.clone().into());
    m.insert(
        "state".into(),
        if f.fixed { "fixed" } else { "noted" }.into(),
    );
    if let Some(c) = &f.commit {
        m.insert("commit".into(), c.clone().into());
    }
    serde_json::Value::Object(m).to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fix(at: i64, sev: Sev, fixed: bool) -> Fix {
        Fix {
            at,
            seat: "a63ab".into(),
            work: "#731".into(),
            sev,
            what: "x".into(),
            fixed,
            commit: fixed.then(|| "abc1234".into()),
        }
    }

    #[test]
    fn lines_round_trip_and_newest_first() {
        let a = fix(100, Sev::High, true);
        let b = fix(300, Sev::Low, false);
        let c = fix(200, Sev::Mid, true);
        let text = [&a, &b, &c].map(to_line).join("\n");
        let got = parse(&text);
        assert_eq!(got.items, vec![b, c, a]);
        assert_eq!((got.fixed(), got.noted()), (2, 1));
    }

    #[test]
    fn bad_rows_are_skipped_and_only_low_may_stay_unfixed() {
        let ok = to_line(&fix(5, Sev::Low, false));
        let text = [
            "not json",
            r#"{"at":1,"seat":"a","work":"w","sev":"X","what":"q","state":"fixed"}"#,
            r#"{"at":1,"seat":"a","work":"w","sev":"H","what":"q","state":"noted"}"#,
            r#"{"at":1,"seat":"a","work":"w","sev":"M","what":"q","state":"noted"}"#,
            r#"{"at":1,"seat":"a","work":"w","sev":"L","what":"  ","state":"noted"}"#,
            r#"{"at":0,"seat":"a","work":"w","sev":"L","what":"q","state":"noted"}"#,
            r#"{"at":1,"seat":"a","work":"w","sev":"L","what":"q","state":"maybe"}"#,
            ok.as_str(),
        ]
        .join("\n");
        let got = parse(&text);
        assert_eq!(got.items, vec![fix(5, Sev::Low, false)]);
    }
}
