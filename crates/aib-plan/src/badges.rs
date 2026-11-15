//! S104 變動標記(DESIGN-plan-badges-519d0-1115 r0 §1):進度欄每次重畫時,拿現在的進度資料跟
//! 「上次看過的快照」(每席一份,狀態夾 plan/seen-<席>.json)比,每個節點最多一個標記。
//!
//! - [完成] status 變成「實查通過」;[更新] status 換了別的詞,或 evidence / owner / title 變了;
//!   [新] 上次沒有這個節點;[退回] 狀態往回走(任何 → 實查不通過、實查通過 → 別的、照進度序往前);
//! - [卡住] status 是開發中 / 審過未推 / 審過排隊,而 updated 超過 [`STUCK_S`] 沒動;
//!   [等你] status「等 Andy」,或節點有 gate(c12gate 的 biometric / human_present,其他值也算)。
//! - 優先序:退回 > 等你 > 完成 > 新 > 更新 > 卡住。
//! - 前四種(比快照得來的)要按「看過了」(進度欄 v 鍵 / aib-plan seen)才清;不會自己消失。
//!   卡住、等你看的是現在的狀態,狀態變了才消失。
//! - 還沒有快照(第一次)⇒ 只有卡住 / 等你,摘要行請人按 v 設起點(不把整棵樹都標成 [新])。
//!
//! 純邏輯:不碰終端機、不碰檔案以外的東西;畫面在 `main.rs`。

use crate::{Plan, PASS};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashSet};

/// 卡住的門檻:updated 超過這麼久沒動(秒;規格「超過 4 小時」)
pub const STUCK_S: i64 = 4 * 3600;

/// 會卡住的狀態(還在做、還沒交出去)
pub const STUCK_STATUSES: &[&str] = &["開發中", "審過未推", "審過排隊"];

/// 等 Andy 的狀態詞
pub const WAIT_ANDY: &str = "等 Andy";

/// 實查不通過(任何狀態變成它都算退回)
pub const FAIL: &str = "實查不通過";

/// 往前走的順序;不在表上的詞(暫停、等 Andy、等前置…)不判斷前後,只看「實查通過 → 別的」。
pub const PROGRESS: &[(&str, u8)] = &[
    ("未開始", 0),
    ("已定未做", 0),
    ("已同意未做", 1),
    ("開發中", 2),
    ("未驗", 3),
    ("審過未推", 4),
    ("審過排隊", 5),
    (PASS, 6),
];

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Badge {
    // 宣告順序 = 優先序(小的先)
    Back,
    WaitYou,
    Done,
    New,
    Updated,
    Stuck,
}

/// 優先序由高到低(摘要行、群組加總照這個順序列)
pub const ORDER: [Badge; 6] = [
    Badge::Back,
    Badge::WaitYou,
    Badge::Done,
    Badge::New,
    Badge::Updated,
    Badge::Stuck,
];

impl Badge {
    /// 畫面上的字(進度欄其他字也只有繁中;四語要等 aib-plan 有語系層,字只在這一處)
    pub fn word(self) -> &'static str {
        match self {
            Badge::Done => "完成",
            Badge::Updated => "更新",
            Badge::New => "新",
            Badge::Back => "退回",
            Badge::Stuck => "卡住",
            Badge::WaitYou => "等你",
        }
    }
}

/// 快照裡一個節點記的欄位
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct Seen {
    #[serde(default)]
    pub status: Option<String>,
    #[serde(default)]
    pub evidence: Option<String>,
    #[serde(default)]
    pub owner: Option<String>,
    #[serde(default)]
    pub title: String,
}

/// plan/seen-<席>.json
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
pub struct Snapshot {
    /// 按「看過了」的時間(epoch 秒)
    pub at: i64,
    pub nodes: BTreeMap<String, Seen>,
}

/// 快照只記 `ids` 裡的節點(進度資料本身的節點;座艙接上去的影子續跑 / 自動審修分支不算)
pub fn snapshot(p: &Plan, ids: &HashSet<String>, at: i64) -> Snapshot {
    Snapshot {
        at,
        nodes: p
            .nodes
            .iter()
            .filter(|n| ids.contains(&n.id))
            .map(|n| {
                (
                    n.id.clone(),
                    Seen {
                        status: n.status.clone(),
                        evidence: n.evidence.clone(),
                        owner: n.owner.clone(),
                        title: n.title.clone(),
                    },
                )
            })
            .collect(),
    }
}

/// 快照檔名裡的席:只收英數字與 `-` `_`,其他(含空的)⇒ `all`(不讓席名走出狀態夾)
pub fn seen_file_name(seat: Option<&str>) -> String {
    let s = seat
        .filter(|s| {
            !s.is_empty()
                && s.chars()
                    .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
        })
        .unwrap_or("all");
    format!("seen-{s}.json")
}

/// 讀快照;沒有檔 ⇒ Ok(None);壞掉 ⇒ Err(畫面照樣畫,摘要行說讀不到)
pub fn read_snapshot(path: &std::path::Path) -> Result<Option<Snapshot>, String> {
    match std::fs::read_to_string(path) {
        Ok(t) => serde_json::from_str(&t)
            .map(Some)
            .map_err(|e| format!("bad_seen_json: {e}")),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("read_seen_failed: {e}")),
    }
}

/// 寫快照:先寫暫存檔再改名(重畫中途讀到一半的檔不會發生)
pub fn write_snapshot(path: &std::path::Path, s: &Snapshot) -> Result<(), String> {
    if let Some(d) = path.parent() {
        std::fs::create_dir_all(d).map_err(|e| format!("write_seen_failed: {e}"))?;
    }
    let tmp = path.with_extension(format!("json.tmp-{}", std::process::id()));
    let text = serde_json::to_string(s).map_err(|e| format!("write_seen_failed: {e}"))?;
    std::fs::write(&tmp, text).map_err(|e| format!("write_seen_failed: {e}"))?;
    std::fs::rename(&tmp, path).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("write_seen_failed: {e}")
    })
}

/// 進度資料原文裡 `gate` 不是 null 的節點 id(main 的 Node 還沒有 gate 欄;讀原文,不改 Node)
pub fn gated_ids(text: &str) -> HashSet<String> {
    let Ok(v) = serde_json::from_str::<serde_json::Value>(text) else {
        return HashSet::new();
    };
    v.get("nodes")
        .and_then(serde_json::Value::as_array)
        .into_iter()
        .flatten()
        .filter(|n| n.get("gate").is_some_and(|g| !g.is_null()))
        .filter_map(|n| {
            n.get("id")
                .and_then(serde_json::Value::as_str)
                .map(str::to_owned)
        })
        .collect()
}

fn rank(s: Option<&str>) -> Option<u8> {
    let s = s?;
    PROGRESS.iter().find(|(w, _)| *w == s).map(|(_, r)| *r)
}

/// 從 `old` 到 `new` 算不算往回走
pub fn went_back(old: Option<&str>, new: Option<&str>) -> bool {
    if old == new {
        return false;
    }
    if new == Some(FAIL) {
        return true;
    }
    if old == Some(PASS) {
        return true;
    }
    matches!((rank(old), rank(new)), (Some(a), Some(b)) if b < a)
}

/// `updated` 欄(「11-14 21:5x」:月-日 時:十分位,台北時間)換成 epoch 秒;年取 `now` 那年,
/// 算出來比現在晚超過一天就當去年。讀不懂 ⇒ None(不判卡住)。
pub fn updated_epoch(s: &str, now: i64) -> Option<i64> {
    const TZ: i64 = 8 * 3600;
    let s = s.trim();
    let (md, hm) = s.split_once(' ')?;
    let (mo, d) = md.split_once('-')?;
    let (hh, mm) = hm.split_once(':')?;
    let num = |x: &str| -> Option<i64> {
        (!x.is_empty() && x.len() <= 2 && x.bytes().all(|b| b.is_ascii_digit()))
            .then(|| x.parse().ok())
            .flatten()
    };
    let (mo, d, hh) = (num(mo)?, num(d)?, num(hh)?);
    // 十分位:「5x」= 50 分;「57」照讀
    let mm = match mm.as_bytes() {
        [t, b'x'] if t.is_ascii_digit() => i64::from(t - b'0') * 10,
        _ => num(mm)?,
    };
    if !(1..=12).contains(&mo) || !(1..=31).contains(&d) || hh > 23 || mm > 59 {
        return None;
    }
    let year = civil_year(now + TZ);
    let at = |y: i64| days_from_civil(y, mo, d) * 86_400 + hh * 3600 + mm * 60 - TZ;
    let t = at(year);
    Some(if t > now + 86_400 { at(year - 1) } else { t })
}

/// 公曆日期 → 1970-01-01 起算的天數(Howard Hinnant 的算法)
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn civil_year(t: i64) -> i64 {
    let z = t.div_euclid(86_400) + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    yoe + era * 400 + i64::from(m <= 2)
}

/// 一個節點的標記(最多一個,照優先序)
pub fn badge_of(
    n: &crate::Node,
    old: Option<&Seen>,
    have_snapshot: bool,
    gated: bool,
    now: i64,
) -> Option<Badge> {
    let st = n.status.as_deref();
    let mut got: Vec<Badge> = Vec::new();
    if let Some(o) = old {
        if went_back(o.status.as_deref(), st) {
            got.push(Badge::Back);
        }
    }
    if st == Some(WAIT_ANDY) || gated {
        got.push(Badge::WaitYou);
    }
    match old {
        Some(o) => {
            if st == Some(PASS) && o.status.as_deref() != Some(PASS) {
                got.push(Badge::Done);
            }
            if o.status.as_deref() != st
                || o.evidence != n.evidence
                || o.owner != n.owner
                || o.title != n.title
            {
                got.push(Badge::Updated);
            }
        }
        None if have_snapshot => {
            if st == Some(PASS) {
                got.push(Badge::Done);
            }
            got.push(Badge::New);
        }
        None => {}
    }
    if st.is_some_and(|s| STUCK_STATUSES.contains(&s))
        && n.updated
            .as_deref()
            .and_then(|u| updated_epoch(u, now))
            .is_some_and(|t| now - t > STUCK_S)
    {
        got.push(Badge::Stuck);
    }
    got.into_iter().min()
}

/// 整棵樹的標記:節點位置 → 標記;只看 `ids` 裡的節點(進度資料本身的)
pub fn compute(
    p: &Plan,
    ids: &HashSet<String>,
    snap: Option<&Snapshot>,
    gated: &HashSet<String>,
    now: i64,
) -> BTreeMap<usize, Badge> {
    p.nodes
        .iter()
        .enumerate()
        .filter(|(_, n)| ids.contains(&n.id))
        .filter_map(|(i, n)| {
            let old = snap.and_then(|s| s.nodes.get(&n.id));
            badge_of(n, old, snap.is_some(), gated.contains(&n.id), now).map(|b| (i, b))
        })
        .collect()
}

/// 各標記幾個(照 [`ORDER`];0 個的不列)
pub fn counts<'a>(it: impl IntoIterator<Item = &'a Badge>) -> Vec<(Badge, usize)> {
    let mut c: BTreeMap<Badge, usize> = BTreeMap::new();
    for b in it {
        *c.entry(*b).or_default() += 1;
    }
    ORDER
        .iter()
        .filter_map(|b| c.get(b).map(|n| (*b, *n)))
        .collect()
}

/// 收合的群組標題後面加的「[完成 2][更新 5]」:底下(不含自己)所有節點的標記加總
pub fn group_tail(
    p: &Plan,
    ix: &crate::Index,
    at: usize,
    marks: &BTreeMap<usize, Badge>,
) -> String {
    let mut stack: Vec<usize> = ix.kids[at].clone();
    let mut seen = HashSet::new();
    let mut found = Vec::new();
    while let Some(i) = stack.pop() {
        if !seen.insert(i) || i >= p.nodes.len() {
            continue;
        }
        if let Some(b) = marks.get(&i) {
            found.push(*b);
        }
        stack.extend(ix.kids[i].iter().copied());
    }
    counts(&found)
        .iter()
        .map(|(b, n)| format!("[{} {n}]", b.word()))
        .collect()
}

/// 最上面那一行;沒東西要說 ⇒ None(畫面不多一行)
pub fn summary(
    marks: &BTreeMap<usize, Badge>,
    snap: Option<&Snapshot>,
    seen_err: Option<&str>,
    clock: impl Fn(i64) -> String,
) -> Option<String> {
    let c = counts(marks.values());
    let body = c
        .iter()
        .map(|(b, n)| format!("{} {n}", b.word()))
        .collect::<Vec<_>>()
        .join(" · ");
    match (seen_err, snap) {
        (Some(e), _) => Some(format!("看過的快照讀不到({e}),按 v 重設起點")),
        (None, None) if c.is_empty() => {
            Some("還沒有看過的起點:按 v 記下現在,之後的變動會標在列前".into())
        }
        (None, None) => Some(format!("{body}(還沒有看過的起點:按 v 記下現在)")),
        (None, Some(_)) if c.is_empty() => None,
        (None, Some(s)) => Some(format!("上次看之後:{body}({} 起,按 v 看過)", clock(s.at))),
    }
}
