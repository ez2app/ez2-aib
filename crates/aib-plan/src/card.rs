//! 卡片指令:`aib-plan mine / set / note / add`(DECISION-council-board-coop-519d0-1117 §2.1)。
//!
//! - `mine`:一席名下最多 7 張卡,推得動的在前 → 靠右的在前 → 擋住別人多的在前 → 停最久的在前;
//!   沒有推得動的就印「沒有可推的卡 ⇒ 停」。
//! - `set` / `note` / `add`:自己搶 `cabin-plan/.lock`、鎖內重讀、只改一個節點、紀錄尾巴加一段
//!   (自己蓋台北「MM-DD HH:MM」,不准整段覆蓋)、照 §2.3 驗進入條件、跑 `gate::check`
//!   (不准多出新錯)、節點數對才原子寫;每次寫一行事件帳 `plan-events.jsonl`。
//!
//! 資料檔是多席共寫的(1 格縮排、檔尾無換行、鍵的順序照原樣):寫回不走整份序列化,
//! 只把那一個節點的文字換掉 ⇒ 其他節點一個位元組都不動。

use crate::gate;
use crate::{Node, Plan, PASS};
use std::collections::{BTreeSet, HashSet};
use std::io::Write as _;
use std::path::{Path, PathBuf};

/// 七站(由左到右);`set` 只收這七個 + 作廢
pub const STATIONS: [&str; 7] = [
    "未開始",
    "已定未做",
    "開發中",
    "審過未推",
    "審過排隊",
    "未驗",
    PASS,
];
pub const VOID: &str = "作廢";
/// 全艦 / 每席的新片上限(Andy 11-17 05:4x)
pub const SLOTS_FLEET: usize = 4;
pub const SLOTS_SEAT: usize = 1;
pub const MINE_ROWS: usize = 7;
const TAIPEI: i64 = 8 * 3600;

/// 狀態詞 ⇒ (站, 旗子)。舊詞照 §2.3 歸站;沒有站(分類節點、作廢、不認得)⇒ None
pub fn station(status: &str) -> Option<(usize, Option<&'static str>)> {
    Some(match status {
        "未開始" => (0, None),
        "已定未做" | "已同意未做" => (1, None),
        "開發中" | "實查不通過" => (2, None),
        "卡住" => (2, Some("卡住")),
        "暫停" => (2, Some("暫停")),
        "等前置" => (1, Some("等前置")),
        "審過未推" => (3, None),
        "審過排隊" => (4, None),
        "未驗" => (5, None),
        PASS | "驗過" => (6, None),
        _ => return None,
    })
}

// ───────────────────────── 專案設定(不寫死這一艙;PLAN-board-loop §6) ─────────────────────────

/// 專案設定檔 `cabin-plan/plan-config.json`(可省;每個欄位都可省,省了用預設):
/// `stations` 七站的字(由左到右,剛好 7 個)、`void` 作廢的字、`slots_seat` / `slots_fleet` 新片上限、
/// `after_write` 寫入成功後跑的指令(argv;`{project}` 換成專案夾 = cabin-plan 的上一層)。
#[derive(Debug, Clone, Default, PartialEq, serde::Deserialize)]
pub struct Config {
    #[serde(default)]
    pub stations: Vec<String>,
    #[serde(default)]
    pub void: Option<String>,
    #[serde(default)]
    pub slots_seat: Option<usize>,
    #[serde(default)]
    pub slots_fleet: Option<usize>,
    #[serde(default)]
    pub after_write: Vec<String>,
}

/// 這個專案的站名(位置固定 0–6 = 未開始 … 驗過;進入條件跟著位置走)
#[derive(Debug, Clone, PartialEq)]
pub struct Words {
    pub names: [String; 7],
    pub void: String,
}

impl Default for Words {
    fn default() -> Words {
        Words {
            names: STATIONS.map(String::from),
            void: VOID.to_string(),
        }
    }
}

impl Words {
    /// 狀態詞 ⇒ (站, 旗子):先認這個專案的字,再認預設字與舊詞
    pub fn station(&self, status: &str) -> Option<(usize, Option<&'static str>)> {
        match self.names.iter().position(|n| n == status) {
            Some(i) => Some((i, None)),
            None => station(status),
        }
    }
    /// `set --status` 收的字 ⇒ 站的位置(作廢 = 7);收這個專案的字、預設七站的字、「驗過」
    pub fn parse(&self, st: &str) -> Option<usize> {
        if st == self.void || st == VOID {
            return Some(7);
        }
        self.names
            .iter()
            .position(|n| n == st)
            .or_else(|| STATIONS.iter().position(|n| *n == st))
            .or((st == "驗過").then_some(6))
    }
    pub fn name(&self, pos: usize) -> &str {
        self.names
            .get(pos)
            .map_or(self.void.as_str(), String::as_str)
    }
}

impl Config {
    pub fn words(&self) -> Result<Words, String> {
        let mut w = Words::default();
        match self.stations.len() {
            0 => {}
            7 => {
                for (slot, name) in w.names.iter_mut().zip(&self.stations) {
                    if name.trim().is_empty() {
                        return Err("bad_config: stations 有空字".into());
                    }
                    *slot = name.trim().to_string();
                }
            }
            n => return Err(format!("bad_config: stations 要剛好 7 個,現在 {n} 個")),
        }
        if let Some(v) = self
            .void
            .as_deref()
            .map(str::trim)
            .filter(|v| !v.is_empty())
        {
            w.void = v.to_string();
        }
        Ok(w)
    }
    pub fn seat_slots(&self) -> usize {
        self.slots_seat.unwrap_or(SLOTS_SEAT)
    }
    pub fn fleet_slots(&self) -> usize {
        self.slots_fleet.unwrap_or(SLOTS_FLEET)
    }
    /// 寫入後掛勾的 argv(`{project}` 換好);沒設 ⇒ None
    pub fn hook(&self, project: &Path) -> Option<Vec<String>> {
        let p = project.to_string_lossy();
        let v: Vec<String> = self
            .after_write
            .iter()
            .map(|a| a.replace("{project}", &p))
            .collect();
        (!v.is_empty() && !v[0].trim().is_empty()).then_some(v)
    }
}

/// 讀 `cabin-plan/plan-config.json`;沒有 ⇒ 預設;壞了 ⇒ 錯(不靜默退回預設)
pub fn load_config(data: &Path) -> Result<Config, String> {
    let p = data.with_file_name("plan-config.json");
    match std::fs::read_to_string(&p) {
        Ok(t) => serde_json::from_str(&t).map_err(|e| format!("bad_config: {}: {e}", p.display())),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Config::default()),
        Err(e) => Err(format!("bad_config: {}: {e}", p.display())),
    }
}

// ───────────────────────── 資料檔的文字:找節點、換一個節點 ─────────────────────────

/// 資料檔文字裡 `nodes` 陣列每個元素的位元組範圍,和陣列最後一個元素的結尾
#[derive(Debug)]
pub struct Spans {
    pub nodes: Vec<(usize, usize)>,
}

fn ws(b: &[u8], mut i: usize) -> usize {
    while i < b.len() && matches!(b[i], b' ' | b'\n' | b'\r' | b'\t') {
        i += 1;
    }
    i
}

fn skip_str(b: &[u8], mut i: usize) -> Result<usize, String> {
    if b.get(i) != Some(&b'"') {
        return Err(format!("bad_json: 位置 {i} 要字串"));
    }
    i += 1;
    while i < b.len() {
        match b[i] {
            b'\\' => i += 2,
            b'"' => return Ok(i + 1),
            _ => i += 1,
        }
    }
    Err("bad_json: 字串沒結束".into())
}

fn skip_value(b: &[u8], i: usize) -> Result<usize, String> {
    let i = ws(b, i);
    match b.get(i) {
        Some(b'"') => skip_str(b, i),
        Some(b'{') | Some(b'[') => {
            let mut depth = 0usize;
            let mut j = i;
            while j < b.len() {
                match b[j] {
                    b'"' => {
                        j = skip_str(b, j)?;
                        continue;
                    }
                    b'{' | b'[' => depth += 1,
                    b'}' | b']' => {
                        depth -= 1;
                        if depth == 0 {
                            return Ok(j + 1);
                        }
                    }
                    _ => {}
                }
                j += 1;
            }
            Err("bad_json: 括號沒結束".into())
        }
        Some(_) => {
            let mut j = i;
            while j < b.len() && !matches!(b[j], b',' | b'}' | b']' | b' ' | b'\n' | b'\r' | b'\t')
            {
                j += 1;
            }
            if j == i {
                return Err(format!("bad_json: 位置 {i} 要值"));
            }
            Ok(j)
        }
        None => Err("bad_json: 檔案提早結束".into()),
    }
}

/// 一個物件的成員:(鍵, 原樣的鍵文字, 原樣的值文字),順序照檔案
pub fn members(obj: &str) -> Result<Vec<(String, String, String)>, String> {
    let b = obj.as_bytes();
    let mut i = ws(b, 0);
    if b.get(i) != Some(&b'{') {
        return Err("bad_json: 要物件".into());
    }
    i = ws(b, i + 1);
    let mut out = Vec::new();
    if b.get(i) == Some(&b'}') {
        return Ok(out);
    }
    loop {
        let k0 = i;
        let k1 = skip_str(b, i)?;
        let raw_key = &obj[k0..k1];
        let key: String = serde_json::from_str(raw_key).map_err(|e| format!("bad_json: {e}"))?;
        i = ws(b, k1);
        if b.get(i) != Some(&b':') {
            return Err("bad_json: 要冒號".into());
        }
        let v0 = ws(b, i + 1);
        let v1 = skip_value(b, v0)?;
        out.push((key, raw_key.to_string(), obj[v0..v1].to_string()));
        i = ws(b, v1);
        match b.get(i) {
            Some(b',') => i = ws(b, i + 1),
            Some(b'}') => return Ok(out),
            _ => return Err("bad_json: 要逗號或 }".into()),
        }
    }
}

/// 找最上層物件的 `nodes` 陣列裡每個元素的範圍
pub fn spans(text: &str) -> Result<Spans, String> {
    let b = text.as_bytes();
    let mut i = ws(b, 0);
    if b.get(i) != Some(&b'{') {
        return Err("bad_json: 最上層要物件".into());
    }
    i = ws(b, i + 1);
    loop {
        let k1 = skip_str(b, i)?;
        let key: String =
            serde_json::from_str(&text[i..k1]).map_err(|e| format!("bad_json: {e}"))?;
        i = ws(b, k1);
        if b.get(i) != Some(&b':') {
            return Err("bad_json: 要冒號".into());
        }
        let v0 = ws(b, i + 1);
        if key == "nodes" {
            if b.get(v0) != Some(&b'[') {
                return Err("bad_json: nodes 要陣列".into());
            }
            let mut nodes = Vec::new();
            let mut j = ws(b, v0 + 1);
            if b.get(j) == Some(&b']') {
                return Ok(Spans { nodes });
            }
            loop {
                let e = skip_value(b, j)?;
                nodes.push((j, e));
                j = ws(b, e);
                match b.get(j) {
                    Some(b',') => j = ws(b, j + 1),
                    Some(b']') => return Ok(Spans { nodes }),
                    _ => return Err("bad_json: nodes 裡要逗號或 ]".into()),
                }
            }
        }
        let v1 = skip_value(b, v0)?;
        i = ws(b, v1);
        match b.get(i) {
            Some(b',') => i = ws(b, i + 1),
            _ => return Err("bad_json: 沒有 nodes".into()),
        }
    }
}

/// 節點物件照資料檔的樣子排(Python json.dumps indent=1:節點在第 2 格、成員在第 3 格)
pub fn render(members: &[(String, String)]) -> String {
    if members.is_empty() {
        return "{}".into();
    }
    let body: Vec<String> = members
        .iter()
        .map(|(k, v)| format!("   {k}: {v}"))
        .collect();
    format!("{{\n{}\n  }}", body.join(",\n"))
}

/// 字串陣列照第 3 格成員的樣子排
pub fn render_str_array(items: &[String]) -> String {
    if items.is_empty() {
        return "[]".into();
    }
    let body: Vec<String> = items.iter().map(|s| format!("    {}", jstr(s))).collect();
    format!("[\n{}\n   ]", body.join(",\n"))
}

fn jstr(s: &str) -> String {
    serde_json::to_string(s).unwrap_or_else(|_| "\"\"".into())
}

/// 換掉一個節點的幾個欄位(沒有的加在最後),其他成員原樣
pub fn patch_node(obj: &str, changes: &[(&str, String)]) -> Result<String, String> {
    let mut m: Vec<(String, String, String)> = members(obj)?;
    for (k, raw) in changes {
        match m.iter_mut().find(|x| x.0 == *k) {
            Some(x) => x.2 = raw.clone(),
            None => m.push((k.to_string(), jstr(k), raw.clone())),
        }
    }
    Ok(render(
        &m.into_iter().map(|(_, rk, v)| (rk, v)).collect::<Vec<_>>(),
    ))
}

// ───────────────────────── 時間 ─────────────────────────

fn civil(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (yoe + era * 400 + i64::from(m <= 2), m, d)
}

fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = if m > 2 { m - 3 } else { m + 9 };
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// 台北 (MM-DD HH:MM, YYYY-MM-DD HH:MM)
pub fn stamps(epoch: i64) -> (String, String) {
    let t = epoch + TAIPEI;
    let (h, mi) = (t.rem_euclid(86_400) / 3600, t.rem_euclid(3600) / 60);
    let (y, m, d) = civil(t.div_euclid(86_400));
    (
        format!("{m:02}-{d:02} {h:02}:{mi:02}"),
        format!("{y:04}-{m:02}-{d:02} {h:02}:{mi:02}"),
    )
}

/// `updated` 欄 ⇒ epoch 秒:收「YYYY-MM-DD HH:MM」與「MM-DD HH:Mx」(x 當 0,年份取不晚於現在的那年)
pub fn parse_updated(s: &str, now: i64) -> Option<i64> {
    let s = s.trim();
    let digit = |c: u8| {
        if c == b'x' {
            Some(0)
        } else {
            (c as char).to_digit(10).map(i64::from)
        }
    };
    let two = |b: &[u8]| Some(digit(*b.first()?)? * 10 + digit(*b.get(1)?)?);
    let (year, rest) = match s.as_bytes().get(4) {
        Some(b'-') => (s.get(..4)?.parse::<i64>().ok(), s.get(5..)?),
        _ => (None, s),
    };
    let b = rest.as_bytes();
    if b.len() < 11 || b[2] != b'-' || b[5] != b' ' || b[8] != b':' {
        return None;
    }
    let (m, d, hh, mm) = (
        two(&b[0..2])?,
        two(&b[3..5])?,
        two(&b[6..8])?,
        two(&b[9..11])?,
    );
    if !(1..=12).contains(&m) || !(1..=31).contains(&d) || hh > 23 || mm > 59 {
        return None;
    }
    let at = |y: i64| days_from_civil(y, m, d) * 86_400 + hh * 3600 + mm * 60 - TAIPEI;
    Some(match year {
        Some(y) => at(y),
        None => {
            let y = civil((now + TAIPEI).div_euclid(86_400)).0;
            if at(y) > now + 86_400 {
                at(y - 1)
            } else {
                at(y)
            }
        }
    })
}

// ───────────────────────── 文字裡的鍵 ─────────────────────────

fn is_hex(s: &str) -> bool {
    !s.is_empty()
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// 拆成「像識別字」的詞:英數與 `._/-:#~`,其他(中文、標點、空白)都當分隔
pub fn tokens(s: &str) -> Vec<&str> {
    s.split(|c: char| !(c.is_ascii_alphanumeric() || "._/-:#~".contains(c)))
        .map(|t| t.trim_matches(|c| c == '.' || c == ':'))
        .filter(|t| !t.is_empty())
        .collect()
}

/// 文字裡的 40 碼(依出現順序)
pub fn sha40s(s: &str) -> Vec<&str> {
    tokens(s)
        .into_iter()
        .flat_map(|t| t.split(['/', ':', '-', '#', '~']))
        .filter(|t| t.len() == 40 && is_hex(t))
        .collect()
}

/// 席碼:5 個字、0-9a-f、至少一個英文字母和一個數字(519d0 a9136 043dd 5d33a f1fae 7b81e)
pub fn is_seat(t: &str) -> bool {
    t.len() == 5
        && is_hex(t)
        && t.bytes().any(|b| b.is_ascii_alphabetic())
        && t.bytes().any(|b| b.is_ascii_digit())
}

fn seat_words(s: &str) -> impl Iterator<Item = &str> {
    s.split(|c: char| !c.is_ascii_alphanumeric())
        .filter(|t| is_seat(t))
}

/// 負責席 = owner 欄第一個席碼
pub fn author(n: &Node) -> Option<String> {
    n.owner
        .as_deref()
        .and_then(|o| seat_words(o).next())
        .map(String::from)
}

/// 這一席在這張卡上的角色;不在 owner / with ⇒ None
pub fn role(n: &Node, seat: &str) -> Option<&'static str> {
    let o = n.owner.as_deref().unwrap_or("");
    if author(n).as_deref() == Some(seat) {
        return Some("作者");
    }
    if let Some(i) = o.find(seat) {
        let before = &o[..i];
        let cut = before
            .rfind([';', '\u{ff1b}', ',', '\u{ff0c}'])
            .map_or(0, |j| j + 1);
        let part = &before[cut..];
        return Some(if part.contains('驗') {
            "驗"
        } else if part.contains('審') {
            "審"
        } else {
            "作者"
        });
    }
    n.with.iter().any(|w| w == seat).then_some("協作")
}

fn file_tokens<'a>(s: &'a str, prefixes: &[&str], ext: &str) -> Vec<&'a str> {
    tokens(s)
        .into_iter()
        .map(|t| t.rsplit('/').next().unwrap_or(t))
        .filter(|t| prefixes.iter().any(|p| t.starts_with(p)) && t.ends_with(ext))
        .collect()
}

fn has_branch(s: &str) -> bool {
    tokens(s).into_iter().any(|t| {
        let Some((kind, rest)) = t.split_once('/') else {
            return false;
        };
        !kind.is_empty()
            && kind.bytes().all(|b| b.is_ascii_lowercase() || b == b'-')
            && rest.len() >= 3
            && rest.contains('-')
            && !t.starts_with("~/")
    })
}

// ───────────────────────── 進入條件(§2.3) ─────────────────────────

/// 進入條件要讀的外面事實;測試換成假的
pub trait Facts {
    /// pending 裡一個檔的文字;讀不到 ⇒ None
    fn pending(&self, name: &str) -> Option<String>;
    /// `rev` 在不在 origin/main 裡;沒有倉 / 讀不到 ⇒ None
    fn in_main(&self, rev: &str) -> Option<bool>;
    /// `a` 是不是 `b` 的祖先(含相等);讀不到 ⇒ None
    fn ancestor(&self, a: &str, b: &str) -> Option<bool>;
    /// PR 開著嗎、頭是什麼;讀不到 ⇒ None
    fn pr(&self, n: u32) -> Option<(bool, String)>;
    /// 以這條分支為頭的最新一張 PR:開著嗎、頭是什麼;沒有 / 讀不到 ⇒ None
    fn pr_of_branch(&self, branch: &str) -> Option<(bool, String)>;
}

/// 查不到在不在 origin/main 時的話
pub const UNKNOWN_MAIN: &str =
    "查不到頭在不在 origin/main(沒有倉:--repo / AIB_PLAN_REPO / drive/refresh-repo)";

/// 收據檔綠、而且是這個頭的。兩種形狀:
/// - receipt-run:HEAD-END == 頭、每行 `RC x=` 都是 0(刀行看 KNIVES-RC)、刀 / 漏咬是 0;
/// - cell-steps(整格 / 安裝頭):`HEAD <40>` == 頭、每個 `STEP rc=0`、`CELL-RED 0`、`END rc=0`、
///   沒有 KILLED / INCOMPLETE / WITHDRAWN;有 SKIP-REUSED(沿用)照實先不判綠(5d33a 9f029128 M3)。
pub fn receipt_problem(text: &str, head: &str) -> Option<String> {
    let cell = !text.lines().any(|l| l.starts_with("HEAD-END "))
        && text
            .lines()
            .any(|l| l.starts_with("CELL-RED ") || l.starts_with("END rc="));
    if cell {
        return cell_problem(text, head);
    }
    let mut rc = 0;
    let mut head_end = false;
    for l in text.lines() {
        let l = l.trim_end_matches('\r');
        if let Some(h) = l.strip_prefix("HEAD-END ") {
            if h.trim() != head {
                return Some(format!("HEAD-END {} 不是 {}", short(h.trim()), short(head)));
            }
            head_end = true;
        } else if let Some(r) = l.strip_prefix("RC ") {
            // 刀那幾行本來就是非 0(want non-0),由 KNIVES-RC 判
            if r.starts_with("knife_") {
                continue;
            }
            rc += 1;
            if !r.trim_end().ends_with("=0") {
                return Some(format!("RC {r} 不是 0"));
            }
        } else if let Some(r) = l.strip_prefix("KNIVES-RC ") {
            if r.trim() != "0" {
                return Some(format!("KNIVES-RC {}", r.trim()));
            }
        } else if let Some(r) = l.strip_prefix("MISSES-COUNT ") {
            if r.trim() != "0" {
                return Some(format!("MISSES-COUNT {}", r.trim()));
            }
        }
    }
    if !head_end {
        return Some("沒有 HEAD-END 行".into());
    }
    (rc == 0).then(|| "沒有 RC 行".into())
}

fn cell_problem(text: &str, head: &str) -> Option<String> {
    let (mut head_ok, mut red0, mut end0, mut steps) = (false, false, false, 0);
    for l in text.lines() {
        let l = l.trim_end_matches('\r');
        let word = l.split_whitespace().next().unwrap_or("");
        if ["KILLED", "INCOMPLETE", "WITHDRAWN", "SKIP-REUSED"]
            .iter()
            .any(|w| word.starts_with(w))
        {
            return Some(format!("{word}:不判綠"));
        }
        if let Some(r) = l.strip_prefix("HEAD ") {
            let h = r.split_whitespace().next().unwrap_or("");
            if h != head {
                return Some(format!("HEAD {} 不是 {}", short(h), short(head)));
            }
            head_ok = true;
        } else if let Some(r) = l.strip_prefix("STEP ") {
            steps += 1;
            if !r.starts_with("rc=0 ") && r != "rc=0" {
                return Some(format!("STEP {r}"));
            }
        } else if let Some(r) = l.strip_prefix("CELL-RED ") {
            if r.trim() != "0" {
                return Some(format!("CELL-RED {}", r.trim()));
            }
            red0 = true;
        } else if let Some(r) = l.strip_prefix("END ") {
            if !r.starts_with("rc=0") {
                return Some(format!("END {r}"));
            }
            end0 = true;
        }
    }
    if !head_ok {
        Some("沒有 HEAD 行".into())
    } else if steps == 0 {
        Some("沒有 STEP 行".into())
    } else if !red0 {
        Some("沒有 CELL-RED 0".into())
    } else if !end0 {
        Some("沒有 END rc=0".into())
    } else {
        None
    }
}

/// 紀錄裡最後一份「通過」審稿審的頭(卡的審過頭);沒有 ⇒ None
fn reviewed_head(record: &str, f: &dyn Facts) -> Option<String> {
    file_tokens(record, &["REVIEW-", "CHECK-", "RECHECK-"], ".md")
        .into_iter()
        .filter_map(|r| {
            let t = f.pending(r)?;
            crate::prs::parse_review(r, t.lines().next().unwrap_or(""))
        })
        .rfind(|rv| rv.pass)
        .map(|rv| rv.head)
}

fn short(s: &str) -> &str {
    s.get(..8).unwrap_or(s)
}

/// 「審過未推」的條件:頭 40 碼 + 審稿(第一行 結論 通過、同頭、審的人不是作者)+ 同頭綠收據
fn reviewed_problems(fact: &str, node: &Node, f: &dyn Facts) -> (Vec<String>, Option<String>) {
    let author = author(node);
    let author = author.as_deref();
    let mut out = Vec::new();
    let Some(head) = sha40s(fact).last().map(|h| h.to_string()) else {
        out.push("紀錄裡沒有頭 40 碼".into());
        return (out, None);
    };
    let reviews = file_tokens(fact, &["REVIEW-", "CHECK-", "RECHECK-"], ".md");
    if reviews.is_empty() {
        out.push("紀錄裡沒有 REVIEW- / CHECK- / RECHECK-….md".into());
    }
    let mut ok_review = false;
    let mut why = Vec::new();
    for r in &reviews {
        let Some(text) = f.pending(r) else {
            why.push(format!("pending/{r} 讀不到"));
            continue;
        };
        let first = text.lines().next().unwrap_or("");
        let Some(rv) = crate::prs::parse_review(r, first) else {
            why.push(format!("{r} 第一行不是「結論: … 頭: <40> H/M/L/N: …」"));
            continue;
        };
        // 審查人欄有值就認它(檔名裡可能還有作者的碼,5d33a N1);沒有才取檔名最後一個席碼
        let seats: Vec<&str> = r
            .trim_end_matches(".md")
            .split('-')
            .filter(|t| is_seat(t))
            .collect();
        let reviewer = node
            .reviewer
            .as_deref()
            .filter(|x| seats.contains(x))
            .or_else(|| seats.last().copied());
        if !rv.pass {
            why.push(format!("{r} 結論不是通過"));
        } else if rv.hmln[0] > 0 {
            why.push(format!("{r} 有 H {} 條", rv.hmln[0]));
        } else if rv.hmln[1] > 0 && !fact.contains("放行") {
            why.push(format!("{r} 有 M {} 條:要寫放行(五項)", rv.hmln[1]));
        } else if rv.head != head {
            why.push(format!(
                "{r} 審的頭 {} 不是 {}",
                short(&rv.head),
                short(&head)
            ));
        } else if reviewer.is_none() {
            why.push(format!("{r} 檔名看不出審的席"));
        } else if reviewer == author {
            why.push(format!("{r} 是作者自己審的"));
        } else {
            ok_review = true;
        }
    }
    if !reviews.is_empty() && !ok_review {
        out.push(why.join(";"));
    }
    let receipts = file_tokens(fact, &["RECEIPT-"], ".out");
    if receipts.is_empty() {
        out.push("紀錄裡沒有同頭收據 RECEIPT-….out".into());
    } else {
        let mut why = Vec::new();
        let ok = receipts.iter().any(|r| match f.pending(r) {
            None => {
                why.push(format!("pending/{r} 讀不到"));
                false
            }
            Some(t) => match receipt_problem(&t, &head) {
                None => true,
                Some(p) => {
                    why.push(format!("{r}:{p}"));
                    false
                }
            },
        });
        if !ok {
            out.push(why.join(";"));
        }
    }
    (out, Some(head))
}

/// 進站 `to`(STATIONS 的位置;作廢 = 7)時新加的那一段 `fact` 缺什麼;空 = 可以進
pub fn entry_problems(to: usize, fact: &str, node: &Node, f: &dyn Facts) -> Vec<String> {
    let a = author(node);
    match to {
        0 => Vec::new(),
        1 => {
            let ok = fact.contains("DECISION")
                || fact.contains("andy-")
                || fact.contains("裁定")
                || node.approved.is_some();
            if ok {
                Vec::new()
            } else {
                vec!["要寫誰同意的:決定檔名(DECISION-…)、andy-…、或「519d0 裁定」".into()]
            }
        }
        2 => {
            if has_branch(fact) || !sha40s(fact).is_empty() {
                Vec::new()
            } else {
                vec!["要寫分支名或頭 40 碼".into()]
            }
        }
        3 => reviewed_problems(fact, node, f).0,
        4 => {
            let (mut out, head) = reviewed_problems(fact, node, f);
            // PR 號與 integ/ 分支都要查到一張開著、頭含這個頭的 PR(5d33a 9f029128 M2)
            let mut asks: Vec<(String, Option<(bool, String)>)> = Vec::new();
            for t in tokens(fact) {
                if let Some(n) = t.strip_prefix('#').and_then(|x| x.parse::<u32>().ok()) {
                    asks.push((format!("#{n}"), f.pr(n)));
                } else if t.starts_with("integ/") {
                    asks.push((t.to_string(), f.pr_of_branch(t)));
                }
            }
            if asks.is_empty() {
                out.push("要寫 #PR 號或 integ/ 分支".into());
            } else if let Some(h) = head.as_deref() {
                let mut why = Vec::new();
                let ok = asks.into_iter().any(|(label, pr)| match pr {
                    None => {
                        why.push(format!("{label} 查不到 PR"));
                        false
                    }
                    Some((false, _)) => {
                        why.push(format!("{label} 沒開著"));
                        false
                    }
                    Some((true, ph)) => {
                        let ok = ph == h || f.ancestor(h, &ph) == Some(true);
                        if !ok {
                            why.push(format!("{label} 的頭 {} 不含 {}", short(&ph), short(h)));
                        }
                        ok
                    }
                });
                if !ok {
                    out.push(why.join(";"));
                }
            }
            out
        }
        5 => {
            // 卡的審過頭要在 origin/main 裡;只寫主線頭不算(5d33a 9f029128 M1)
            let record = format!("{} {fact}", node.evidence.as_deref().unwrap_or(""));
            let Some(h) = reviewed_head(&record, f) else {
                return vec![
                    "紀錄裡找不到通過的審稿頭(REVIEW- / CHECK- / RECHECK- 第一行 結論: 通過 頭: <40>)"
                        .into(),
                ];
            };
            match f.in_main(&h) {
                Some(true) => Vec::new(),
                Some(false) => vec![format!("審過的頭 {} 不在 origin/main 裡", short(&h))],
                None => vec![UNKNOWN_MAIN.into()],
            }
        }
        6 => {
            let mut out = Vec::new();
            let verifier = fact
                .split("驗\u{ff1a}")
                .skip(1)
                .chain(fact.split("驗:").skip(1))
                .filter_map(|r| seat_words(r.trim_start()).next())
                .next();
            match verifier {
                None => out.push("要寫「驗:<席碼>」".into()),
                Some(v) if Some(v) == a.as_deref() => out.push("驗的人是作者".into()),
                Some(_) => {}
            }
            let evidence = tokens(fact).into_iter().any(|t| {
                let base = t.rsplit('/').next().unwrap_or(t);
                [".md", ".out", ".json", ".jsonl", ".txt", ".log", ".tsv"]
                    .iter()
                    .any(|e| base.ends_with(e))
                    || (base.len() == 64 && is_hex(base))
                    || (base.len() >= 10 && base.bytes().all(|b| b.is_ascii_digit()))
            }) || fact
                .split("job ")
                .skip(1)
                .any(|r| r.starts_with(|c: char| c.is_ascii_digit()));
            if !evidence {
                out.push(
                    "要寫證據檔名、sha256 或 job 編號(10 位以上數字,或寫「job <編號>」)".into(),
                );
            }
            out
        }
        _ => {
            if fact.trim().chars().count() < 2 {
                vec!["作廢要寫原因".into()]
            } else {
                Vec::new()
            }
        }
    }
}

// ───────────────────────── mine ─────────────────────────

#[derive(Debug, Clone, PartialEq, serde::Serialize)]
pub struct Row {
    pub id: String,
    pub station: String,
    pub role: String,
    pub head: Option<String>,
    pub waiting: Option<String>,
    pub age_h: Option<i64>,
    pub next: String,
    pub machine: bool,
    pub pushable: bool,
    pub blocks: usize,
}

/// 名下的卡,排好序(全部;印的時候取前 7)
pub fn mine_rows(p: &Plan, seat: &str, slot_nodes: &HashSet<String>, now: i64) -> Vec<Row> {
    mine_rows_with(p, seat, slot_nodes, now, &Words::default())
}

/// [`mine_rows`],站名照這個專案的字
pub fn mine_rows_with(
    p: &Plan,
    seat: &str,
    slot_nodes: &HashSet<String>,
    now: i64,
    w: &Words,
) -> Vec<Row> {
    let status_of = |id: &str| {
        p.nodes
            .iter()
            .find(|n| n.id == id)
            .and_then(|n| n.status.clone())
    };
    let mut rows: Vec<Row> = p
        .nodes
        .iter()
        .filter_map(|n| {
            let st = n.status.as_deref()?;
            let (s, flag) = w.station(st)?;
            if s == 6 {
                return None;
            }
            let role = role(n, seat)?;
            let open_deps: Vec<String> = n
                .after
                .iter()
                .filter(|d| match d.strip_prefix("node:") {
                    Some(t) => status_of(t).as_deref() != Some(PASS),
                    None => false,
                })
                .cloned()
                .collect();
            let blocks = p
                .nodes
                .iter()
                .filter(|m| m.status.as_deref() != Some(PASS))
                .filter(|m| {
                    m.after
                        .iter()
                        .any(|d| d.strip_prefix("node:") == Some(&n.id))
                })
                .count();
            let has_slot = slot_nodes.contains(&n.id);
            let (next, machine) = match s {
                0 | 1 if !has_slot => ("佔名額", false),
                0 | 1 => ("開工", true),
                2 => ("送審", true),
                3 => ("進疊", false),
                4 => ("等合", false),
                _ => ("驗", false),
            };
            let waiting = if let Some(f) = flag {
                Some(f.to_string())
            } else if !open_deps.is_empty() {
                Some(format!("等 {}", open_deps.join(" ")))
            } else if s == 4 {
                Some("等整合合".into())
            } else if s <= 1 && !has_slot {
                Some("要先佔新片名額".into())
            } else {
                None
            };
            let pushable = waiting.is_none() && role != "協作";
            let head = n
                .evidence
                .as_deref()
                .and_then(|e| sha40s(e).last().map(|h| h[..8].to_string()));
            let age_h = n
                .updated
                .as_deref()
                .and_then(|u| parse_updated(u, now))
                .map(|t| (now - t).max(0) / 3600);
            Some(Row {
                id: n.id.clone(),
                station: w.name(s).to_string(),
                role: role.to_string(),
                head,
                waiting,
                age_h,
                next: next.to_string(),
                machine,
                pushable,
                blocks,
            })
        })
        .collect();
    let rank = |r: &Row| w.names.iter().position(|s| *s == r.station).unwrap_or(0);
    rows.sort_by(|a, b| {
        b.pushable
            .cmp(&a.pushable)
            .then(rank(b).cmp(&rank(a)))
            .then(b.blocks.cmp(&a.blocks))
            .then(b.age_h.unwrap_or(0).cmp(&a.age_h.unwrap_or(0)))
            .then(a.id.cmp(&b.id))
    });
    rows
}

/// 新片名額登記檔 `slots.tsv`(資料檔同夾):每行「席碼<TAB>節點<TAB>時間」,# 開頭是註解
pub fn read_slots(text: &str) -> Vec<(String, String)> {
    text.lines()
        .filter(|l| !l.trim().is_empty() && !l.starts_with('#'))
        .filter_map(|l| {
            let mut it = l.split('\t');
            Some((it.next()?.trim().to_string(), it.next()?.trim().to_string()))
        })
        .collect()
}

pub fn format_row(r: &Row) -> String {
    format!(
        "{}{} | {} | {} | {} | {} | 停 {} | {} | {}",
        if r.pushable { "" } else { "(灰)" },
        r.station,
        r.id,
        r.role,
        r.head.as_deref().unwrap_or("—"),
        r.waiting.as_deref().unwrap_or("推得動"),
        r.age_h.map_or("?".into(), |h| format!("{h} 時")),
        r.next,
        if r.machine {
            "佔機器"
        } else {
            "不佔機器"
        }
    )
}

// ───────────────────────── 寫:鎖、事件帳、set / note / add ─────────────────────────

/// `cabin-plan/.lock`(四步規則的同一把鎖);離開範圍就放
pub struct Lock(PathBuf);

impl Lock {
    pub fn take(data: &Path, now: i64) -> Result<Lock, String> {
        let dir = data
            .parent()
            .ok_or("bad_path: 資料檔沒有上層夾")?
            .join(".lock");
        match std::fs::create_dir(&dir) {
            Ok(()) => Ok(Lock(dir)),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                let age = std::fs::metadata(&dir)
                    .and_then(|m| m.modified())
                    .ok()
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| now - d.as_secs() as i64);
                Err(format!(
                    "locked: 進度樹被鎖著({},{} 秒前建的);等它放掉再跑",
                    dir.display(),
                    age.map_or("?".into(), |a| a.to_string())
                ))
            }
            Err(e) => Err(format!("lock_failed: {e}")),
        }
    }
}

impl Drop for Lock {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir(&self.0);
    }
}

/// 一次寫入要做的事
#[derive(Debug, Clone)]
pub enum Op {
    Set {
        id: String,
        status: Option<String>,
        fact: String,
        head: Option<String>,
        wait: Option<String>,
        /// 審查人欄 `reviewer`(Andy 11-17 06:3x §6-4)
        reviewer: Option<String>,
        /// 真人需求欄 `need`:Andy 的原話或他要的結果;沒有就不給,不准編(§6-5)
        need: Option<String>,
    },
    Note {
        id: String,
        fact: String,
    },
    Add {
        parent: String,
        id: Option<String>,
        title: String,
        owner: String,
        why: Option<String>,
        reviewer: Option<String>,
        need: Option<String>,
    },
}

/// 寫好的新文字 + 事件帳那一行要的欄位
#[derive(Debug, Clone, PartialEq)]
pub struct Applied {
    pub text: String,
    pub id: String,
    pub from: Option<String>,
    pub to: Option<String>,
    pub fact: String,
}

fn segment(old: Option<&str>, at: &str, seat: &str, fact: &str) -> String {
    let seg = format!("{at} {seat} {}", fact.trim());
    match old.map(str::trim) {
        None | Some("") | Some("—") => seg,
        Some(o) => format!("{o} | {seg}"),
    }
}

/// 在文字上做一次寫入(純函式:鎖、讀檔、事件帳、檢查在外面)
pub fn apply(text: &str, op: &Op, seat: &str, now: i64, f: &dyn Facts) -> Result<Applied, String> {
    apply_with(text, op, seat, now, f, &Words::default())
}

/// [`apply`],站名照這個專案的字
pub fn apply_with(
    text: &str,
    op: &Op,
    seat: &str,
    now: i64,
    f: &dyn Facts,
    w: &Words,
) -> Result<Applied, String> {
    let plan = crate::parse(text)?;
    let sp = spans(text)?;
    if sp.nodes.len() != plan.nodes.len() {
        return Err(format!(
            "bad_json: 找到 {} 個節點、解析出 {} 個",
            sp.nodes.len(),
            plan.nodes.len()
        ));
    }
    let (at, full) = stamps(now);
    let find = |id: &str| {
        plan.nodes
            .iter()
            .position(|n| n.id == id)
            .ok_or_else(|| format!("no_such_node: {id}"))
    };
    let splice = |i: usize, new_obj: &str| {
        let (a, b) = sp.nodes[i];
        format!("{}{}{}", &text[..a], new_obj, &text[b..])
    };
    match op {
        Op::Set {
            id,
            status,
            fact,
            head,
            wait,
            reviewer,
            need,
        } => {
            if fact.trim().is_empty() {
                return Err("missing: --add 要寫這一段的事實".into());
            }
            let i = find(id)?;
            let n = &plan.nodes[i];
            let mut fact = fact.trim().to_string();
            if let Some(h) = head {
                if h.len() != 40 || !is_hex(h) {
                    return Err(format!("bad_value: --head {h} 不是 40 碼"));
                }
                if !fact.contains(h.as_str()) {
                    fact = format!("{fact} 頭 {h}");
                }
            }
            let mut changes: Vec<(&str, String)> = Vec::new();
            let mut to = None;
            if let Some(st) = status {
                let Some(pos) = w.parse(st) else {
                    return Err(format!(
                        "bad_value: --status {st}(只收 {} / {})",
                        w.names.join(" / "),
                        w.void
                    ));
                };
                let k = w.name(pos);
                if n.status.as_deref() != Some(k) {
                    let miss = entry_problems(pos, &fact, n, f);
                    if !miss.is_empty() {
                        return Err(format!("entry: 進「{k}」還缺:{}", miss.join(";")));
                    }
                }
                changes.push(("status", jstr(k)));
                to = Some(k.to_string());
            }
            if let Some(w) = wait {
                let w = if w.starts_with("node:")
                    || w.starts_with("pr:")
                    || w.starts_with("skills-pr:")
                {
                    w.clone()
                } else if let Some(n) = w.strip_prefix('#') {
                    format!("pr:{n}")
                } else {
                    format!("node:{w}")
                };
                if gate::parse_dep(&w).is_none() {
                    return Err(format!("bad_value: --wait {w}"));
                }
                let mut after = n.after.clone();
                if !after.contains(&w) {
                    after.push(w);
                }
                changes.push(("after", render_str_array(&after)));
            }
            if let Some(r) = reviewer {
                if !is_seat(r) {
                    return Err(format!("bad_value: --reviewer {r} 不是席碼"));
                }
                changes.push(("reviewer", jstr(r)));
            }
            if let Some(x) = need.as_deref().map(str::trim).filter(|x| !x.is_empty()) {
                changes.push(("need", jstr(x)));
            }
            changes.push((
                "evidence",
                jstr(&segment(n.evidence.as_deref(), &at, seat, &fact)),
            ));
            changes.push(("updated", jstr(&full)));
            let (a, b) = sp.nodes[i];
            let obj = patch_node(&text[a..b], &changes)?;
            Ok(Applied {
                text: splice(i, &obj),
                id: id.clone(),
                from: n.status.clone(),
                to,
                fact,
            })
        }
        Op::Note { id, fact } => {
            if fact.trim().is_empty() {
                return Err("missing: note 要寫事實".into());
            }
            let i = find(id)?;
            let n = &plan.nodes[i];
            let changes = vec![
                (
                    "evidence",
                    jstr(&segment(n.evidence.as_deref(), &at, seat, fact)),
                ),
                ("updated", jstr(&full)),
            ];
            let (a, b) = sp.nodes[i];
            let obj = patch_node(&text[a..b], &changes)?;
            Ok(Applied {
                text: splice(i, &obj),
                id: id.clone(),
                from: n.status.clone(),
                to: None,
                fact: fact.trim().to_string(),
            })
        }
        Op::Add {
            parent,
            id,
            title,
            owner,
            why,
            reviewer,
            need,
        } => {
            find(parent)?;
            if let Some(r) = reviewer.as_deref().filter(|r| !is_seat(r)) {
                return Err(format!("bad_value: --reviewer {r} 不是席碼"));
            }
            if title.trim().is_empty() {
                return Err("missing: --title".into());
            }
            let ids: BTreeSet<&str> = plan.nodes.iter().map(|n| n.id.as_str()).collect();
            let id = match id {
                Some(x) if ids.contains(x.as_str()) => return Err(format!("exists: {x} 已經有了")),
                Some(x) if x.trim().is_empty() || x.contains(char::is_whitespace) => {
                    return Err(format!("bad_value: --id {x}"))
                }
                Some(x) => x.clone(),
                None => (1..)
                    .map(|k| format!("{parent}-c{k}"))
                    .find(|c| !ids.contains(c.as_str()))
                    .unwrap_or_default(),
            };
            let fact = format!("開卡:{}", why.as_deref().unwrap_or("(待補)"));
            let mut m: Vec<(String, String)> = vec![
                (jstr("id"), jstr(&id)),
                (jstr("parent"), jstr(parent)),
                (jstr("kind"), jstr("task")),
                (jstr("title"), jstr(title.trim())),
            ];
            if let Some(w) = why {
                m.push((jstr("why"), jstr(w.trim())));
            }
            m.push((jstr("status"), jstr(w.name(0))));
            m.push((jstr("owner"), jstr(owner)));
            if let Some(r) = reviewer {
                m.push((jstr("reviewer"), jstr(r)));
            }
            if let Some(x) = need.as_deref().map(str::trim).filter(|x| !x.is_empty()) {
                m.push((jstr("need"), jstr(x)));
            }
            m.push((jstr("evidence"), jstr(&segment(None, &at, seat, &fact))));
            m.push((jstr("updated"), jstr(&full)));
            let obj = render(&m);
            let Some(&(_, last_end)) = sp.nodes.last() else {
                return Err("bad_json: nodes 是空的".into());
            };
            let new = format!("{},\n  {}{}", &text[..last_end], obj, &text[last_end..]);
            Ok(Applied {
                text: new,
                id,
                from: None,
                to: Some(w.name(0).to_string()),
                fact,
            })
        }
    }
}

/// 寫入前的最後一道:節點數對、`gate::check` 沒有多出新錯
pub fn verify(
    old: &str,
    new: &str,
    added: bool,
    pending: Option<&HashSet<String>>,
) -> Result<(), String> {
    let a = crate::parse(old)?;
    let b = crate::parse(new).map_err(|e| format!("new_bad: 改完的檔解析不了:{e}"))?;
    let want = a.nodes.len() + usize::from(added);
    if b.nodes.len() != want {
        return Err(format!("count: 節點數 {} 應該是 {want}", b.nodes.len()));
    }
    let before: HashSet<String> = gate::check(&a, pending)
        .iter()
        .map(|p| p.to_string())
        .collect();
    let fresh: Vec<String> = gate::check(&b, pending)
        .iter()
        .map(|p| p.to_string())
        .filter(|p| !before.contains(p))
        .collect();
    if fresh.is_empty() {
        Ok(())
    } else {
        Err(format!(
            "check: 改完多出 {} 個錯:{}",
            fresh.len(),
            fresh.join(";")
        ))
    }
}

/// 事件帳一行
pub fn event_line(op: &str, seat: &str, now: i64, a: &Applied) -> String {
    serde_json::json!({
        "ts": now,
        "at": stamps(now).0,
        "by": seat,
        "op": op,
        "id": a.id,
        "from": a.from,
        "to": a.to,
        "fact": a.fact,
    })
    .to_string()
}

/// pending 夾(資料檔在 pending/<夾>/cabin-plan/data.json)
pub fn pending_dir(data: &Path) -> Option<PathBuf> {
    let dir = data.parent()?.parent()?.parent()?;
    (dir.file_name()? == "pending").then(|| dir.to_path_buf())
}

/// 鎖 → 鎖內讀 → 改 → 檢查 → 原子寫 → 事件帳 → 放鎖
pub fn write(data: &Path, op: &Op, seat: &str, now: i64, f: &dyn Facts) -> Result<Applied, String> {
    write_with(data, op, seat, now, f, &Words::default())
}

/// [`write`],站名照這個專案的字
pub fn write_with(
    data: &Path,
    op: &Op,
    seat: &str,
    now: i64,
    f: &dyn Facts,
    w: &Words,
) -> Result<Applied, String> {
    let _lock = Lock::take(data, now)?;
    let old = std::fs::read_to_string(data).map_err(|e| format!("read_failed: {e}"))?;
    let a = apply_with(&old, op, seat, now, f, w)?;
    let pending: Option<HashSet<String>> = pending_dir(data).and_then(|d| {
        std::fs::read_dir(d).ok().map(|rd| {
            rd.filter_map(Result::ok)
                .filter_map(|e| e.file_name().into_string().ok())
                .collect()
        })
    });
    verify(
        &old,
        &a.text,
        matches!(op, Op::Add { .. }),
        pending.as_ref(),
    )?;
    let tmp = data.with_extension(format!("json.aib-plan-{}", std::process::id()));
    std::fs::write(&tmp, &a.text).map_err(|e| format!("write_failed: {e}"))?;
    if let Err(e) = std::fs::rename(&tmp, data) {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("write_failed: {e}"));
    }
    let name = match op {
        Op::Set { .. } => "set",
        Op::Note { .. } => "note",
        Op::Add { .. } => "add",
    };
    let line = event_line(name, seat, now, &a);
    let log = data.with_file_name("plan-events.jsonl");
    std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log)
        .and_then(|mut fh| writeln!(fh, "{line}"))
        .map_err(|e| {
            format!(
                "event_failed: 資料已寫、事件帳沒寫上({}):{e}",
                log.display()
            )
        })?;
    Ok(a)
}

// ───────────────────────── 真的事實 + 命令列 ─────────────────────────

const GIT_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(20);
const GH_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(30);
const GH_REPO: &str = "ez2app/ez2-aib";
const HOOK_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(60);

/// 讀 pending 檔、問 git(本機倉)、問 gh(一張一個期限)
pub struct RealFacts {
    pub pending: Option<PathBuf>,
    pub repo: Option<PathBuf>,
}

fn safe_rev(r: &str) -> bool {
    !r.is_empty()
        && !r.starts_with('-')
        && r.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"/._-".contains(&b))
}

impl RealFacts {
    fn git_bool(&self, args: &[&str]) -> Option<bool> {
        let repo = self.repo.as_ref()?;
        let mut a = vec!["-C".to_string(), repo.to_string_lossy().into_owned()];
        a.extend(args.iter().map(|s| s.to_string()));
        match aib_ghread::run_fixed_any("git", &a, GIT_TIMEOUT) {
            Ok((Some(0), _)) => Some(true),
            Ok((Some(1), _)) => Some(false),
            _ => None,
        }
    }
}

impl Facts for RealFacts {
    fn pending(&self, name: &str) -> Option<String> {
        if name.contains(['/', '\\']) || name.starts_with('.') {
            return None;
        }
        std::fs::read_to_string(self.pending.as_ref()?.join(name)).ok()
    }
    fn in_main(&self, rev: &str) -> Option<bool> {
        if !safe_rev(rev) {
            return None;
        }
        // 短碼要先認得出來;認不得(rc 128)= 不知道,不是「不在」
        self.git_bool(&["cat-file", "-e", &format!("{rev}^{{commit}}")])?
            .then_some(())?;
        self.git_bool(&["merge-base", "--is-ancestor", rev, "origin/main"])
    }
    fn ancestor(&self, a: &str, b: &str) -> Option<bool> {
        if !safe_rev(a) || !safe_rev(b) {
            return None;
        }
        self.git_bool(&["merge-base", "--is-ancestor", a, b])
    }
    fn pr(&self, n: u32) -> Option<(bool, String)> {
        let args = [
            "pr",
            "view",
            &n.to_string(),
            "-R",
            GH_REPO,
            "--json",
            "state,headRefOid",
        ]
        .map(String::from);
        let out = aib_ghread::run_fixed("gh", &args, GH_TIMEOUT).ok()?;
        let v: serde_json::Value = serde_json::from_slice(&out).ok()?;
        Some((
            v.get("state")?.as_str()? == "OPEN",
            v.get("headRefOid")?.as_str()?.to_string(),
        ))
    }
    fn pr_of_branch(&self, branch: &str) -> Option<(bool, String)> {
        if !safe_rev(branch) {
            return None;
        }
        let args = [
            "pr",
            "list",
            "-R",
            GH_REPO,
            "--head",
            branch,
            "--state",
            "all",
            "--limit",
            "1",
            "--json",
            "state,headRefOid",
        ]
        .map(String::from);
        let out = aib_ghread::run_fixed("gh", &args, GH_TIMEOUT).ok()?;
        let v: serde_json::Value = serde_json::from_slice(&out).ok()?;
        let p = v.as_array()?.first()?;
        Some((
            p.get("state")?.as_str()? == "OPEN",
            p.get("headRefOid")?.as_str()?.to_string(),
        ))
    }
}

/// 驗「未驗」要的本機倉:--repo、AIB_PLAN_REPO、AIB_DRIVE_REPO_DIR、~/.ez2-aib/drive/refresh-repo 的內容
pub fn repo_dir(flag: Option<PathBuf>, home: &Path) -> Option<PathBuf> {
    flag.or_else(|| std::env::var_os("AIB_PLAN_REPO").map(PathBuf::from))
        .or_else(|| std::env::var_os("AIB_DRIVE_REPO_DIR").map(PathBuf::from))
        .or_else(|| {
            let t =
                std::fs::read_to_string(home.join(".ez2-aib").join("drive").join("refresh-repo"))
                    .ok()?;
            let t = t.trim();
            (!t.is_empty()).then(|| PathBuf::from(t))
        })
        .filter(|p| p.is_dir())
}

pub const USAGE: &str = "usage:
  aib-plan mine [--seat 席] [--json] [--data FILE]
  aib-plan set <編號> [--status 站] --add \"事實\" [--head 40碼] [--wait 編號|#PR] [--reviewer 席] [--need \"Andy 原話\"] [--by 席] [--repo DIR] [--data FILE]
  aib-plan note <編號> \"事實\" [--by 席] [--data FILE]
  aib-plan add <父編號> --title 標題 [--seat 負責席] [--why 為什麼] [--id 編號] [--reviewer 席] [--need \"Andy 原話\"] [--by 席] [--data FILE]
站:未開始 / 已定未做 / 開發中 / 審過未推 / 審過排隊 / 未驗 / 實查通過(= 驗過)/ 作廢
rc:0 寫了 / 1 讀不到資料 / 2 參數或進入條件不對 / 4 被鎖著";

/// `mine` / `set` / `note` / `add`;回傳結束碼
pub fn cli(
    argv: &[String],
    default_data: PathBuf,
    home: &Path,
    seat_guess: Option<String>,
    now: i64,
) -> i32 {
    let mut it = argv.iter();
    let cmd = it.next().map(String::as_str).unwrap_or("");
    let mut pos: Vec<String> = Vec::new();
    let flag =
        |k: &str, v: Option<&String>, out: &mut Vec<(String, String)>| -> Result<(), String> {
            out.push((
                k.to_string(),
                v.ok_or(format!("missing_value: {k}"))?.clone(),
            ));
            Ok(())
        };
    let mut kv: Vec<(String, String)> = Vec::new();
    let mut json = false;
    while let Some(a) = it.next() {
        let r = match a.as_str() {
            "--json" => {
                json = true;
                Ok(())
            }
            "-h" | "--help" => {
                println!("{USAGE}");
                return 0;
            }
            k @ ("--seat" | "--data" | "--status" | "--add" | "--head" | "--wait" | "--by"
            | "--repo" | "--title" | "--why" | "--id" | "--reviewer" | "--need") => {
                flag(k, it.next(), &mut kv)
            }
            k if k.starts_with("--") => Err(format!("unknown_arg: {k}")),
            v => {
                pos.push(v.to_string());
                Ok(())
            }
        };
        if let Err(e) = r {
            eprintln!("{e}\n{USAGE}");
            return 2;
        }
    }
    let get = |k: &str| kv.iter().rev().find(|x| x.0 == k).map(|x| x.1.clone());
    let data = get("--data").map(PathBuf::from).unwrap_or(default_data);
    let by = get("--by")
        .or_else(|| std::env::var("AIB_SEAT").ok())
        .or(seat_guess);
    let (cfg, words) = match load_config(&data).and_then(|c| c.words().map(|w| (c, w))) {
        Ok(x) => x,
        Err(e) => {
            eprintln!("{e}");
            return 2;
        }
    };
    match cmd {
        "mine" => {
            let Some(seat) = get("--seat").or(by) else {
                eprintln!("要 --seat(或在席位窗格裡跑)");
                return 2;
            };
            let p = match std::fs::read_to_string(&data)
                .map_err(|e| e.to_string())
                .and_then(|t| crate::parse(&t))
            {
                Ok(p) => p,
                Err(e) => {
                    eprintln!("read_failed: {}: {e}", data.display());
                    return 1;
                }
            };
            let slots = std::fs::read_to_string(data.with_file_name("slots.tsv"))
                .ok()
                .map(|t| read_slots(&t));
            let mine_slots: HashSet<String> = slots
                .iter()
                .flatten()
                .filter(|(s, _)| *s == seat)
                .map(|(_, n)| n.clone())
                .collect();
            let rows = mine_rows_with(&p, &seat, &mine_slots, now, &words);
            let stop = !rows.iter().any(|r| r.pushable);
            let quota = match &slots {
                Some(s) => format!(
                    "新片名額 你 {}/{}、全艦 {}/{}",
                    mine_slots.len(),
                    cfg.seat_slots(),
                    s.len(),
                    cfg.fleet_slots()
                ),
                None => "新片名額:沒有登記檔 slots.tsv(照 dm 向統籌佔)".into(),
            };
            if json {
                let v = serde_json::json!({
                    "seat": seat,
                    "total": rows.len(),
                    "rows": rows.iter().take(MINE_ROWS).collect::<Vec<_>>(),
                    "quota": quota,
                    "stop": stop,
                });
                println!("{v}");
            } else {
                println!("@{seat} 名下 {} 張卡(列前 {}):站 | 編號 | 角色 | 頭 | 卡在什麼 | 停多久 | 下一步 | 機器", rows.len(), MINE_ROWS.min(rows.len()));
                for r in rows.iter().take(MINE_ROWS) {
                    println!("{}", format_row(r));
                }
                println!("{quota}");
                if stop {
                    println!("沒有可推的卡 ⇒ 停");
                }
            }
            0
        }
        "set" | "note" | "add" => {
            let Some(seat) = by.filter(|s| is_seat(s)) else {
                eprintln!("要 --by 席碼(或在席位窗格裡跑)");
                return 2;
            };
            let Some(first) = pos.first().cloned() else {
                eprintln!("{USAGE}");
                return 2;
            };
            let op = match cmd {
                "set" => Op::Set {
                    id: first,
                    status: get("--status"),
                    fact: get("--add").unwrap_or_default(),
                    head: get("--head"),
                    wait: get("--wait"),
                    reviewer: get("--reviewer"),
                    need: get("--need"),
                },
                "note" => Op::Note {
                    id: first,
                    fact: pos.get(1).cloned().or(get("--add")).unwrap_or_default(),
                },
                _ => Op::Add {
                    parent: first,
                    id: get("--id"),
                    title: get("--title").unwrap_or_default(),
                    owner: get("--seat").unwrap_or_else(|| seat.clone()),
                    why: get("--why"),
                    reviewer: get("--reviewer"),
                    need: get("--need"),
                },
            };
            let facts = RealFacts {
                pending: pending_dir(&data),
                repo: repo_dir(get("--repo").map(PathBuf::from), home),
            };
            match write_with(&data, &op, &seat, now, &facts, &words) {
                Ok(a) => {
                    println!(
                        "寫了 {}:{} → {}|{}",
                        a.id,
                        a.from.as_deref().unwrap_or("—"),
                        a.to.as_deref().unwrap_or("(不變)"),
                        a.fact
                    );
                    // 寫入後掛勾(例如重產看板資料):失敗只說一聲,寫入已成立
                    let project = data
                        .parent()
                        .and_then(Path::parent)
                        .unwrap_or(Path::new("."));
                    if let Some(argv) = cfg.hook(project) {
                        match aib_ghread::run_fixed_any(&argv[0], &argv[1..], HOOK_TIMEOUT) {
                            Ok((Some(0), _)) => {}
                            Ok((code, _)) => eprintln!("寫入後掛勾 {} 結束碼 {code:?}", argv[0]),
                            Err(e) => eprintln!("寫入後掛勾沒跑成:{e}"),
                        }
                    }
                    0
                }
                Err(e) => {
                    eprintln!("沒寫:{e}");
                    if e.starts_with("locked") {
                        4
                    } else if e.starts_with("read_failed") {
                        1
                    } else {
                        2
                    }
                }
            }
        }
        _ => {
            eprintln!("{USAGE}");
            2
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// 照 Python json.dumps(indent=1, ensure_ascii=False) 的樣子(資料檔的寫法)
    const DATA: &str = "{\n \"seats\": {\n  \"a9136\": {\n   \"name\": \"甲\"\n  }\n },\n \"nodes\": [\n  {\n   \"id\": \"S1\",\n   \"parent\": null,\n   \"kind\": \"goal\",\n   \"title\": \"主線 \\\"一\\\"\\n二\",\n   \"evidence\": \"—\"\n  },\n  {\n   \"id\": \"S2\",\n   \"parent\": \"S1\",\n   \"title\": \"卡\",\n   \"status\": \"開發中\",\n   \"owner\": \"a9136;審 5d33a\",\n   \"evidence\": \"11-16 05:1x 起 | 頭 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\n   \"updated\": \"11-16 05:1x\",\n   \"after\": [\n    \"node:S3\"\n   ],\n   \"approved\": {\n    \"at\": \"x\",\n    \"by\": \"Andy\",\n    \"pointer\": \"DECISION-x.md §1\"\n   }\n  },\n  {\n   \"id\": \"S3\",\n   \"parent\": \"S1\",\n   \"title\": \"前置\",\n   \"status\": \"未驗\",\n   \"owner\": \"043dd\",\n   \"after\": []\n  }\n ],\n \"asof\": \"11-17\",\n \"cabin\": \"dab9\"\n}";
    const H: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    const NOW: i64 = 1_794_870_000; // 2026-11-17 台北早上

    struct F {
        files: Vec<(&'static str, String)>,
        main: Option<bool>,
        pr: Option<(bool, String)>,
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
        fn pr(&self, _: u32) -> Option<(bool, String)> {
            self.pr.clone()
        }
        fn pr_of_branch(&self, _: &str) -> Option<(bool, String)> {
            self.pr.clone()
        }
    }
    fn none() -> F {
        F {
            files: vec![],
            main: None,
            pr: None,
        }
    }
    fn green(head: &str) -> String {
        format!("x\nHEAD-END {head}\nRC fmt=0\nRC test=0\nRC knife_A1=101 (want non-0)\nKNIVES-RC 0\nMISSES-COUNT 0\n")
    }
    fn review(head: &str, verdict: &str) -> String {
        format!("結論: {verdict} 頭: {head} H/M/L/N: 0/0/0/1 範圍: 全文\n")
    }

    #[test]
    fn every_node_renders_back_byte_for_byte() {
        let sp = spans(DATA).unwrap();
        assert_eq!(sp.nodes.len(), 3);
        for &(a, b) in &sp.nodes {
            let m = members(&DATA[a..b]).unwrap();
            let back = render(&m.into_iter().map(|(_, k, v)| (k, v)).collect::<Vec<_>>());
            assert_eq!(back, &DATA[a..b]);
        }
        assert_eq!(
            render_str_array(&["node:S3".into()]),
            "[\n    \"node:S3\"\n   ]"
        );
    }

    #[test]
    fn note_appends_a_stamped_segment_and_touches_only_that_node() {
        let a = apply(
            DATA,
            &Op::Note {
                id: "S2".into(),
                fact: " 審過 ".into(),
            },
            "5d33a",
            NOW,
            &none(),
        )
        .unwrap();
        let (at, full) = stamps(NOW);
        let p = crate::parse(&a.text).unwrap();
        let n = &p.nodes[1];
        assert_eq!(
            n.evidence.as_deref().unwrap(),
            format!(
                "11-16 05:1x 起 | 頭 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa | {at} 5d33a 審過"
            )
        );
        assert_eq!(n.updated.as_deref(), Some(full.as_str()));
        assert_eq!(n.status.as_deref(), Some("開發中"));
        // 別的節點、鍵的順序、檔尾都沒動
        let sp0 = spans(DATA).unwrap();
        let sp1 = spans(&a.text).unwrap();
        assert_eq!(&DATA[..sp0.nodes[1].0], &a.text[..sp1.nodes[1].0]);
        assert_eq!(&DATA[sp0.nodes[1].1..], &a.text[sp1.nodes[1].1..]);
        assert!(!a.text.ends_with('\n'));
        let keys: Vec<String> = members(&a.text[sp1.nodes[1].0..sp1.nodes[1].1])
            .unwrap()
            .into_iter()
            .map(|m| m.0)
            .collect();
        assert_eq!(
            keys,
            [
                "id", "parent", "title", "status", "owner", "evidence", "updated", "after",
                "approved"
            ]
        );
        assert!(verify(DATA, &a.text, false, None).is_ok());
    }

    #[test]
    fn set_refuses_a_station_whose_entry_is_missing_and_says_what() {
        let set = |st: &str, fact: &str, f: &F| {
            apply(
                DATA,
                &Op::Set {
                    id: "S2".into(),
                    status: Some(st.into()),
                    fact: fact.into(),
                    head: None,
                    wait: None,
                    reviewer: None,
                    need: None,
                },
                "a9136",
                NOW,
                f,
            )
        };
        let e = set("審過未推", "審完了", &none()).unwrap_err();
        assert!(e.contains("頭 40 碼"), "{e}");
        let fact = format!("{H} REVIEW-x-5d33a-bbbbbbbb.md RECEIPT-lin-a9136-r1-bbbbbbbbb.out");
        // 審稿不通過 / 審別的頭 / 作者自己審 / 收據紅
        let mk = |rv: String, rc: String| F {
            files: vec![
                ("REVIEW-x-5d33a-bbbbbbbb.md", rv),
                ("RECEIPT-lin-a9136-r1-bbbbbbbbb.out", rc),
            ],
            main: None,
            pr: None,
        };
        assert!(set("審過未推", &fact, &mk(review(H, "不通過"), green(H)))
            .unwrap_err()
            .contains("不是通過"));
        let other = "c".repeat(40);
        assert!(
            set("審過未推", &fact, &mk(review(&other, "通過"), green(H)))
                .unwrap_err()
                .contains("審的頭")
        );
        assert!(
            set("審過未推", &fact, &mk(review(H, "通過"), green(&other)))
                .unwrap_err()
                .contains("HEAD-END")
        );
        let red = green(H).replace("RC test=0", "RC test=101");
        assert!(set("審過未推", &fact, &mk(review(H, "通過"), red))
            .unwrap_err()
            .contains("RC test=101"));
        let selff = fact.replace("5d33a", "a9136");
        let f = F {
            files: vec![
                ("REVIEW-x-a9136-bbbbbbbb.md", review(H, "通過")),
                ("RECEIPT-lin-a9136-r1-bbbbbbbbb.out", green(H)),
            ],
            main: None,
            pr: None,
        };
        assert!(set("審過未推", &selff, &f)
            .unwrap_err()
            .contains("作者自己審"));
        // 齊了就寫
        let a = set("審過未推", &fact, &mk(review(H, "通過"), green(H))).unwrap();
        assert_eq!(a.to.as_deref(), Some("審過未推"));
        assert_eq!(a.from.as_deref(), Some("開發中"));
        // 排隊:PR 要開著、頭含這個頭
        let q = format!("{fact} #900");
        let mut f = mk(review(H, "通過"), green(H));
        f.pr = Some((false, H.into()));
        assert!(set("審過排隊", &q, &f).unwrap_err().contains("沒開著"));
        f.pr = Some((true, other.clone()));
        assert!(set("審過排隊", &q, &f).unwrap_err().contains("不含"));
        f.pr = Some((true, H.into()));
        assert!(set("審過排隊", &q, &f).is_ok());
        // 排隊寫 integ/ 也要查到開著、含這個頭的 PR(M2)
        let qi = format!("{fact} integ/batch2-f1fae-1117");
        f.pr = None;
        assert!(set("審過排隊", &qi, &f).unwrap_err().contains("查不到 PR"));
        f.pr = Some((true, H.into()));
        assert!(set("審過排隊", &qi, &f).is_ok());
        // 未驗:卡的審過頭要在 origin/main 裡;只寫主線頭不算(M1);查不到就說查不到
        assert!(set("未驗", "main 1f9a749cc", &f)
            .unwrap_err()
            .contains("找不到通過的審稿頭"));
        let merged = "合進 main 1f9a749cc REVIEW-x-5d33a-bbbbbbbb.md";
        f.main = None;
        assert!(set("未驗", merged, &f).unwrap_err().contains("查不到"));
        f.main = Some(false);
        assert!(set("未驗", merged, &f)
            .unwrap_err()
            .contains("不在 origin/main"));
        f.main = Some(true);
        assert!(set("未驗", merged, &f).is_ok());
        // 審稿通過卻有 H ⇒ 不過;有 M 要寫放行(L1)
        let hm = |h: u32, m: u32| format!("結論: 通過 頭: {H} H/M/L/N: {h}/{m}/0/0 範圍: 全文\n");
        assert!(set("審過未推", &fact, &mk(hm(1, 0), green(H)))
            .unwrap_err()
            .contains("有 H"));
        assert!(set("審過未推", &fact, &mk(hm(0, 1), green(H)))
            .unwrap_err()
            .contains("放行"));
        assert!(set(
            "審過未推",
            &format!("{fact} 放行五項齊"),
            &mk(hm(0, 1), green(H))
        )
        .is_ok());
        // 整格收據(cell-steps)也認(M3)
        let cell = |end: &str| {
            format!("HEAD {H}  START x\nSTEP rc=0 1s  fmt\nSTEP rc=0 3s  test\nCELL-RED 0\n{end}\n")
        };
        assert!(set(
            "審過未推",
            &fact,
            &mk(review(H, "通過"), cell("END rc=0 x"))
        )
        .is_ok());
        assert!(set(
            "審過未推",
            &fact,
            &mk(review(H, "通過"), cell("END rc=1 x"))
        )
        .unwrap_err()
        .contains("END rc=1"));
        let red_step = cell("END rc=0 x").replace("STEP rc=0 3s", "STEP rc=101 3s");
        assert!(set("審過未推", &fact, &mk(review(H, "通過"), red_step))
            .unwrap_err()
            .contains("STEP rc=101"));
        let reused = format!("{}SKIP-REUSED test\n", cell("END rc=0 x"));
        assert!(set("審過未推", &fact, &mk(review(H, "通過"), reused))
            .unwrap_err()
            .contains("SKIP-REUSED"));
        // 驗過:驗的人不是作者 + 證據
        assert!(set("實查通過", "驗:a9136 CHECK-x.md", &none())
            .unwrap_err()
            .contains("作者"));
        assert!(set("實查通過", "驗:5d33a 11-17 20261117", &none())
            .unwrap_err()
            .contains("證據"));
        assert!(set("實查通過", "驗:5d33a job 55512345678", &none()).is_ok());
        assert!(set("實查通過", "驗:5d33a 看過了", &none())
            .unwrap_err()
            .contains("證據"));
        assert!(
            set("驗過", "驗:5d33a CHECK-x.md", &none())
                .unwrap()
                .to
                .as_deref()
                == Some(PASS)
        );
        // 開發中要分支或頭;不認得的詞
        assert!(set("開發中", "寫了", &none()).is_ok(), "同站不驗");
        assert!(set("已定未做", "隨便", &none()).is_ok(), "有 approved 欄");
        assert!(set("已同意未做", "x", &none())
            .unwrap_err()
            .contains("只收"));
        assert!(set(VOID, "", &none()).unwrap_err().contains("--add"));
    }

    #[test]
    fn set_head_and_wait_land_in_the_record_and_after() {
        let a = apply(
            DATA,
            &Op::Set {
                id: "S3".into(),
                status: None,
                fact: "接 main".into(),
                head: Some(H.into()),
                wait: Some("#831".into()),
                reviewer: Some("5d33a".into()),
                need: Some(" Andy:「今天要可用」 ".into()),
            },
            "043dd",
            NOW,
            &none(),
        )
        .unwrap();
        let p = crate::parse(&a.text).unwrap();
        assert_eq!(p.nodes[2].after, ["pr:831"]);
        let sp = spans(&a.text).unwrap();
        let (x, y) = sp.nodes[2];
        let m = members(&a.text[x..y]).unwrap();
        let get = |k: &str| m.iter().find(|r| r.0 == k).map(|r| r.2.clone());
        assert_eq!(get("reviewer").as_deref(), Some("\"5d33a\""));
        assert_eq!(get("need").as_deref(), Some("\"Andy:「今天要可用」\""));
        assert!(p.nodes[2]
            .evidence
            .as_deref()
            .unwrap()
            .ends_with(&format!("043dd 接 main 頭 {H}")));
        assert!(apply(
            DATA,
            &Op::Set {
                id: "S3".into(),
                status: None,
                fact: "x".into(),
                head: Some("abc".into()),
                wait: None,
                reviewer: None,
                need: None,
            },
            "043dd",
            NOW,
            &none()
        )
        .is_err());
    }

    #[test]
    fn add_opens_a_card_under_a_parent_with_a_free_id() {
        let op = Op::Add {
            parent: "S1".into(),
            id: None,
            title: "裝上這台".into(),
            owner: "a9136".into(),
            why: None,
            reviewer: None,
            need: None,
        };
        let a = apply(DATA, &op, "a9136", NOW, &none()).unwrap();
        let p = crate::parse(&a.text).unwrap();
        assert_eq!(p.nodes.len(), 4);
        let n = &p.nodes[3];
        assert_eq!(
            (n.id.as_str(), n.parent.as_deref(), n.status.as_deref()),
            ("S1-c1", Some("S1"), Some("未開始"))
        );
        assert!(n
            .evidence
            .as_deref()
            .unwrap()
            .ends_with("a9136 開卡:(待補)"));
        assert!(verify(DATA, &a.text, true, None).is_ok());
        assert!(verify(DATA, &a.text, false, None)
            .unwrap_err()
            .contains("節點數"));
        // 前三個節點的文字不動
        assert_eq!(
            &a.text[..spans(DATA).unwrap().nodes[2].1],
            &DATA[..spans(DATA).unwrap().nodes[2].1]
        );
        let dup = Op::Add {
            parent: "S1".into(),
            id: Some("S2".into()),
            title: "x".into(),
            owner: "a9136".into(),
            why: None,
            reviewer: None,
            need: None,
        };
        assert!(apply(DATA, &dup, "a9136", NOW, &none())
            .unwrap_err()
            .contains("已經有了"));
        let orphan = Op::Add {
            parent: "NOPE".into(),
            id: None,
            title: "x".into(),
            owner: "a9136".into(),
            why: None,
            reviewer: None,
            need: None,
        };
        assert!(apply(DATA, &orphan, "a9136", NOW, &none()).is_err());
    }

    #[test]
    fn verify_refuses_a_change_that_adds_a_check_error() {
        let bad = DATA.replace("\"node:S3\"", "\"node:NOPE\"");
        assert!(verify(DATA, &bad, false, None)
            .unwrap_err()
            .contains("多出"));
        // 原本就有的錯不擋別人
        assert!(verify(&bad, &bad, false, None).is_ok());
    }

    #[test]
    fn mine_puts_pushable_and_right_first_and_says_stop_when_none() {
        let p = crate::parse(DATA).unwrap();
        let rows = mine_rows(&p, "a9136", &HashSet::new(), NOW);
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].waiting.as_deref(), Some("等 node:S3"));
        assert!(!rows[0].pushable);
        assert_eq!(rows[0].head.as_deref(), Some("aaaaaaaa"));
        let r = mine_rows(&p, "5d33a", &HashSet::new(), NOW);
        assert_eq!(r[0].role, "審");
        let r = mine_rows(&p, "043dd", &HashSet::new(), NOW);
        assert_eq!(
            (r[0].station.as_str(), r[0].next.as_str(), r[0].pushable),
            ("未驗", "驗", true)
        );
        assert!(format_row(&rows[0]).starts_with("(灰)開發中 | S2 | 作者 | aaaaaaaa | 等 node:S3"));
    }

    #[test]
    fn small_parsers() {
        let (at, full) = stamps(NOW);
        assert_eq!(parse_updated(&full, NOW).map(|t| t / 60), Some(NOW / 60));
        assert_eq!(parse_updated(&at, NOW).map(|t| t / 60), Some(NOW / 60));
        assert!(parse_updated("11-17 05:1x", NOW).is_some());
        assert!(parse_updated("—", NOW).is_none());
        assert!(has_branch("分支 feat/plan-mine-set-a9136-1117 開工"));
        assert!(!has_branch("~/.ez2-aib/pending/x.md"));
        assert!(is_seat("519d0") && is_seat("f1fae") && !is_seat("12345") && !is_seat("abcde"));
        assert_eq!(
            read_slots("# x\na9136\tS107\t11-17\n\n"),
            [("a9136".to_string(), "S107".to_string())]
        );
        assert_eq!(
            receipt_problem("RC a=0\n", H).as_deref(),
            Some("沒有 HEAD-END 行")
        );
        assert_eq!(station("等前置"), Some((1, Some("等前置"))));
        assert_eq!(station("x"), None);
    }

    #[test]
    fn write_takes_the_lock_writes_whole_and_logs_one_event() {
        let d = tempfile::tempdir().unwrap();
        let data = d.path().join("data.json");
        std::fs::write(&data, DATA).unwrap();
        let op = Op::Note {
            id: "S3".into(),
            fact: "看過".into(),
        };
        // 別人拿著鎖 ⇒ 不寫、說被鎖著
        let held = Lock::take(&data, NOW).unwrap();
        let e = write(&data, &op, "5d33a", NOW, &none()).unwrap_err();
        assert!(e.starts_with("locked"), "{e}");
        assert_eq!(std::fs::read_to_string(&data).unwrap(), DATA);
        drop(held);
        assert!(!d.path().join(".lock").exists());
        let a = write(&data, &op, "5d33a", NOW, &none()).unwrap();
        assert_eq!(std::fs::read_to_string(&data).unwrap(), a.text);
        assert!(!d.path().join(".lock").exists(), "放了鎖");
        let log = std::fs::read_to_string(d.path().join("plan-events.jsonl")).unwrap();
        let v: serde_json::Value = serde_json::from_str(log.trim()).unwrap();
        assert_eq!(
            (v["op"].as_str(), v["id"].as_str(), v["by"].as_str()),
            (Some("note"), Some("S3"), Some("5d33a"))
        );
        // 進入條件不對 ⇒ 檔不動、事件帳不加
        let bad = Op::Set {
            id: "S3".into(),
            status: Some("審過未推".into()),
            fact: "x".into(),
            head: None,
            wait: None,
            reviewer: None,
            need: None,
        };
        assert!(write(&data, &bad, "043dd", NOW, &none()).is_err());
        assert_eq!(std::fs::read_to_string(&data).unwrap(), a.text);
        assert_eq!(
            std::fs::read_to_string(d.path().join("plan-events.jsonl"))
                .unwrap()
                .lines()
                .count(),
            1
        );
        assert!(!d.path().join(".lock").exists());
    }

    #[test]
    fn the_reviewer_field_picks_the_reviewer_out_of_a_file_name_with_two_seats() {
        let data = DATA.replace(
            "\"owner\": \"a9136;審 5d33a\",",
            "\"owner\": \"a9136;審 5d33a\",\n   \"reviewer\": \"5d33a\",",
        );
        let rv = "REVIEW-spec-5d33a-a9136-r0.md";
        let f = F {
            files: vec![
                (rv, review(H, "通過")),
                ("RECEIPT-lin-a9136-r1-bbbbbbbbb.out", green(H)),
            ],
            main: None,
            pr: None,
        };
        let op = |d: &str| {
            apply(
                d,
                &Op::Set {
                    id: "S2".into(),
                    status: Some("審過未推".into()),
                    fact: format!("{H} {rv} RECEIPT-lin-a9136-r1-bbbbbbbbb.out"),
                    head: None,
                    wait: None,
                    reviewer: None,
                    need: None,
                },
                "a9136",
                NOW,
                &f,
            )
        };
        // 沒有審查人欄:取檔名最後一個席碼 = 作者 ⇒ 擋;有審查人欄 ⇒ 認它
        assert!(op(DATA).unwrap_err().contains("作者自己審"));
        assert!(op(&data).is_ok());
    }

    #[test]
    fn a_project_config_renames_the_stations_sets_the_quota_and_the_hook() {
        let cfg: Config = serde_json::from_str(
            r#"{"stations": ["todo", "agreed", "dev", "reviewed", "queued", "merged", "done"],
                "void": "dropped", "slots_seat": 2, "after_write": ["node", "{project}/board/gen.js"]}"#,
        )
        .unwrap();
        let w = cfg.words().unwrap();
        assert_eq!((cfg.seat_slots(), cfg.fleet_slots()), (2, SLOTS_FLEET));
        assert_eq!(
            cfg.hook(Path::new("/p")).unwrap(),
            ["node", "/p/board/gen.js"]
        );
        assert_eq!(
            (
                w.parse("dev"),
                w.parse("開發中"),
                w.parse("dropped"),
                w.parse("驗過")
            ),
            (Some(2), Some(2), Some(7), Some(6))
        );
        // 寫進去的是這個專案的字;進入條件跟著位置走
        let op = Op::Set {
            id: "S3".into(),
            status: Some("開發中".into()),
            fact: "分支 feat/x-a9136-1117".into(),
            head: None,
            wait: None,
            reviewer: None,
            need: None,
        };
        let a = apply_with(DATA, &op, "043dd", NOW, &none(), &w).unwrap();
        assert_eq!(a.to.as_deref(), Some("dev"));
        let p = crate::parse(&a.text).unwrap();
        assert_eq!(p.nodes[2].status.as_deref(), Some("dev"));
        let rows = mine_rows_with(&p, "043dd", &HashSet::new(), NOW, &w);
        assert_eq!(rows[0].station, "dev");
        // 七站不足 / 沒設定檔 / 壞檔
        let bad: Config = serde_json::from_str(r#"{"stations": ["a"]}"#).unwrap();
        assert!(bad.words().unwrap_err().contains('7'));
        assert_eq!(Config::default().hook(Path::new("/p")), None);
        let d = tempfile::tempdir().unwrap();
        let data = d.path().join("data.json");
        assert_eq!(load_config(&data).unwrap(), Config::default());
        std::fs::write(d.path().join("plan-config.json"), "{").unwrap();
        assert!(load_config(&data).unwrap_err().starts_with("bad_config"));
    }
}
