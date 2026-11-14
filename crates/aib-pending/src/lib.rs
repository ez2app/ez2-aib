//! S81 p-1a(PLAN-s81-p1-aib-pending-b433b-1114 r2):pending 資料夾的共用規則。
//!
//! - 檔頭:`---` 開頭與結尾之間的 `key: value`,`#` 之後是註解;不認識的鍵保留。
//! - 種類:看檔名開頭(第一個 `-` 之前),表只有這一處。
//! - 不能搬的:排除表(EXCLUDE-pending-519d0-1114.md)+ 寫死在程式裡的規則。
//!   來源讀不到 = 不知道 = 當成不能搬(寧可少搬，不搬錯)。
//!
//! 只讀:這裡沒有任何寫檔、搬檔、刪檔的函式。封存工具(p-4 / p-5)呼叫這一份，不另寫一套。

use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

/// 排除表的檔名(本身也不能搬)
pub const EXCLUDE_FILE: &str = "EXCLUDE-pending-519d0-1114.md";
/// aib-pending 自己產生的索引(不掃自己、不封存)
pub const INDEX_FILE: &str = "INDEX.json";

// ---------- 檔頭 ----------

/// 檔頭欄位(鍵照原樣、值去頭尾空白)
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Header {
    pub fields: BTreeMap<String, String>,
}

impl Header {
    pub fn get(&self, key: &str) -> Option<&str> {
        self.fields.get(key).map(String::as_str)
    }
}

/// 檔頭寫壞了
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HeaderError {
    /// 有開頭的 `---`、沒有結尾的 `---`
    Unclosed,
}

impl fmt::Display for HeaderError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            HeaderError::Unclosed => write!(f, "檔頭有開頭的 --- 但沒有結尾的 ---"),
        }
    }
}

/// 讀檔頭。第一行不是 `---` ⇒ Ok(None)(沒有檔頭)。
pub fn parse_header(text: &str) -> Result<Option<Header>, HeaderError> {
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let mut lines = text.lines();
    if lines.next().map(str::trim_end) != Some("---") {
        return Ok(None);
    }
    let mut h = Header::default();
    for line in lines {
        let line = line.trim_end();
        if line == "---" {
            return Ok(Some(h));
        }
        let body = match line.find('#') {
            Some(i) => &line[..i],
            None => line,
        };
        if let Some((k, v)) = body.split_once(':') {
            let k = k.trim();
            if !k.is_empty() {
                h.fields.insert(k.to_string(), v.trim().to_string());
            }
        }
    }
    Err(HeaderError::Unclosed)
}

// ---------- 種類 ----------

/// 會演進的文件(要寫檔頭)
pub const EVOLVING: &[&str] = &[
    "PLAN", "DEV", "DESIGN", "SPEC", "TODO", "IFACE", "DECISION", "RULES",
];
/// 紀錄(寫完就不再改)
pub const RECORD: &[&str] = &[
    "RELAY",
    "REVIEW",
    "REVIEWREQ",
    "RECEIPT",
    "TEST",
    "HANDOFF",
    "OPINION",
    "SUMMARY",
    "FIXLOG",
    "FOLLOWUP",
    "DM",
    "DMTRIAGE",
    "ASK",
    "DIFF",
    "DEEPREVIEW",
    "AUDIT",
    "BRIEF",
    "BUNDLE",
    "CLEANUP",
    "PREP",
    "ALERT",
    "SWEEP",
    "REF",
];
/// 綁頭碼的紀錄:頭碼還沒進 main(或分支還在)就不能搬(1ced7 11-14 12:58)
pub const HEAD_BOUND: &[&str] = &["REVIEW", "RECEIPT", "TEST", "FIXLOG"];

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Kind {
    /// 會演進(開頭字,大寫)
    Evolving(String),
    /// 紀錄(開頭字,大寫)
    Record(String),
    /// 不認得的開頭:列出來給人補表，不猜
    Unknown,
}

/// 檔名第一個 `-` 之前、轉大寫(比對不分大小寫:Mac / Windows 檔名都不分)
pub fn prefix(name: &str) -> String {
    name.split(['-', '.'])
        .next()
        .unwrap_or("")
        .to_ascii_uppercase()
}

pub fn classify(name: &str) -> Kind {
    let p = prefix(name);
    if EVOLVING.contains(&p.as_str()) {
        Kind::Evolving(p)
    } else if RECORD.contains(&p.as_str()) {
        Kind::Record(p)
    } else {
        Kind::Unknown
    }
}

// ---------- 排除表 ----------

/// 排除表裡讀出來的路徑樣式(`*` 萬用;`/` 結尾 = 資料夾)
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ExcludeTable {
    pub patterns: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TableError {
    /// 一條都沒讀到:不當成「沒有排除」
    Empty,
}

impl fmt::Display for TableError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            TableError::Empty => write!(f, "排除表一條都沒讀到(不當成沒有排除)"),
        }
    }
}

/// 去掉括號裡的說明(半形與全形,可巢狀)
fn drop_parens(s: &str) -> String {
    let mut out = String::new();
    let mut depth = 0usize;
    for c in s.chars() {
        match c {
            '(' | '\u{ff08}' => depth += 1,
            ')' | '\u{ff09}' => depth = depth.saturating_sub(1),
            _ if depth == 0 => out.push(c),
            _ => {}
        }
    }
    out
}

/// 一個字像不像路徑樣式:只有檔名字元,有 `.` `/` `*` 其中之一，至少一個英文字
fn path_like(t: &str) -> bool {
    !t.is_empty()
        && t.chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-' | '*' | '/'))
        && t.contains(['.', '/', '*'])
        && t.chars().any(|c| c.is_ascii_alphabetic())
        && !t.starts_with(['.', '/'])
        && !t.contains("..")
}

/// 讀排除表:`- ` 開頭的行，去括號說明，用逗號 / 頓號 / 空白切，留像路徑的字。
pub fn parse_exclude_table(text: &str) -> Result<ExcludeTable, TableError> {
    let mut seen = BTreeSet::new();
    let mut patterns = Vec::new();
    for line in text.lines() {
        let Some(item) = line.trim_start().strip_prefix("- ") else {
            continue;
        };
        let item = drop_parens(&item.replace("**", ""));
        for t in item.split([',', '\u{ff0c}', '、', ':', '\u{ff1a}', ' ', '\t', '`']) {
            let t = t.trim();
            if path_like(t) && seen.insert(t.to_ascii_lowercase()) {
                patterns.push(t.to_string());
            }
        }
    }
    if patterns.is_empty() {
        return Err(TableError::Empty);
    }
    Ok(ExcludeTable { patterns })
}

/// `*` 萬用比對(不分大小寫)
pub fn glob(pat: &str, name: &str) -> bool {
    let p: Vec<char> = pat.to_lowercase().chars().collect();
    let n: Vec<char> = name.to_lowercase().chars().collect();
    let (mut pi, mut ni) = (0, 0);
    let (mut star, mut mark) = (None, 0);
    while ni < n.len() {
        if pi < p.len() && p[pi] != '*' && p[pi] == n[ni] {
            pi += 1;
            ni += 1;
        } else if pi < p.len() && p[pi] == '*' {
            star = Some(pi);
            pi += 1;
            mark = ni;
        } else if let Some(s) = star {
            pi = s + 1;
            mark += 1;
            ni = mark;
        } else {
            return false;
        }
    }
    p[pi..].iter().all(|&c| c == '*')
}

impl ExcludeTable {
    /// 命中的那條樣式。資料夾樣式(`/` 結尾)只比資料夾;子資料夾裡的檔(含 `/`)不在這裡比。
    pub fn matches(&self, name: &str, is_dir: bool) -> Option<&str> {
        self.patterns
            .iter()
            .find(|pat| match pat.strip_suffix('/') {
                Some(dir) => is_dir && glob(dir, name),
                None => !pat.contains('/') && glob(pat, name),
            })
            .map(String::as_str)
    }
}

// ---------- 不能搬的判斷 ----------

/// pending 頂層的一項
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    /// 檔名(不含路徑)
    pub name: String,
    pub is_dir: bool,
    /// 修改時間(秒);找每席最新 HANDOFF 用
    pub mtime: u64,
    /// 讀到的檔頭(沒有 / 讀不到 = None)
    pub header: Option<Header>,
}

/// 外部來源:讀到了,或讀不到
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Source<T> {
    Known(T),
    Unknown,
}

/// 綁頭碼紀錄的頭碼狀態
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HeadState {
    /// 頭碼已在 main,或分支已不存在
    Settled,
    /// 頭碼還沒進 main、分支還在
    Open,
}

/// 判斷要用的外部資料(由呼叫端讀;讀不到給 Unknown)
#[derive(Debug, Clone)]
pub struct Ctx {
    /// 每席最新一份 HANDOFF 的檔名(用 [`latest_handoffs`] 算)
    pub latest_handoff: BTreeSet<String>,
    /// 開著的 PR 引用的 pending 檔名(小寫)
    pub open_pr_refs: Source<BTreeSet<String>>,
    /// 綁頭碼紀錄 → 頭碼狀態(小寫檔名);沒列到的 = 不知道
    pub heads: Source<BTreeMap<String, HeadState>>,
}

/// 為什麼不能搬
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Reason {
    /// 子資料夾(第一版整批不動)
    Subfolder,
    /// PUSH-FREEZE*:路徑本身是約定
    PushFreeze,
    /// 排除表本身或 INDEX.json
    OwnFile,
    /// 檔頭寫 status: 常駐
    Resident,
    /// 某席最新一份 HANDOFF(狀態列鉤子與 aib handoff 依檔名找)
    LatestHandoff,
    /// 開著的 PR 引用它
    OpenPrRef,
    /// 讀不到哪些 PR 開著:不知道就不搬
    OpenPrUnknown,
    /// 頭碼還沒進 main、分支還在
    HeadOpen,
    /// 讀不到頭碼狀態:不知道就不搬
    HeadUnknown,
    /// 排除表的這一條
    Table(String),
}

impl fmt::Display for Reason {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Reason::Subfolder => write!(f, "子資料夾(第一版整批不動)"),
            Reason::PushFreeze => write!(f, "PUSH-FREEZE 路徑是約定"),
            Reason::OwnFile => write!(f, "排除表 / INDEX.json 本身"),
            Reason::Resident => write!(f, "檔頭 status: 常駐"),
            Reason::LatestHandoff => write!(f, "這一席最新一份 HANDOFF"),
            Reason::OpenPrRef => write!(f, "開著的 PR 引用它"),
            Reason::OpenPrUnknown => write!(f, "讀不到哪些 PR 開著(不知道就不搬)"),
            Reason::HeadOpen => write!(f, "頭碼還沒進 main、分支還在"),
            Reason::HeadUnknown => write!(f, "讀不到頭碼狀態(不知道就不搬)"),
            Reason::Table(p) => write!(f, "排除表:{p}"),
        }
    }
}

/// HANDOFF-<席>-… 的席碼(5 碼小寫英數)
fn handoff_seat(name: &str) -> Option<String> {
    if prefix(name) != "HANDOFF" {
        return None;
    }
    let seat = name.split('-').nth(1)?;
    (seat.len() == 5 && seat.chars().all(|c| c.is_ascii_alphanumeric()))
        .then(|| seat.to_ascii_lowercase())
}

/// 每席最新一份 HANDOFF(看 mtime;同秒取檔名大的)
pub fn latest_handoffs(entries: &[Entry]) -> BTreeSet<String> {
    let mut best: BTreeMap<String, (u64, &str)> = BTreeMap::new();
    for e in entries.iter().filter(|e| !e.is_dir) {
        let Some(seat) = handoff_seat(&e.name) else {
            continue;
        };
        let cand = (e.mtime, e.name.as_str());
        let slot = best.entry(seat).or_insert(cand);
        if cand > *slot {
            *slot = cand;
        }
    }
    best.into_values().map(|(_, n)| n.to_string()).collect()
}

/// 這一項能不能搬:不能 ⇒ Some(第一個理由)
pub fn excluded(e: &Entry, table: &ExcludeTable, ctx: &Ctx) -> Option<Reason> {
    if e.is_dir {
        return Some(Reason::Subfolder);
    }
    let p = prefix(&e.name);
    if p == "PUSH" && e.name.to_ascii_uppercase().starts_with("PUSH-FREEZE") {
        return Some(Reason::PushFreeze);
    }
    if e.name.eq_ignore_ascii_case(EXCLUDE_FILE) || e.name.eq_ignore_ascii_case(INDEX_FILE) {
        return Some(Reason::OwnFile);
    }
    if e.header
        .as_ref()
        .and_then(|h| h.get("status"))
        .is_some_and(|s| s == "常駐")
    {
        return Some(Reason::Resident);
    }
    if ctx.latest_handoff.contains(&e.name) {
        return Some(Reason::LatestHandoff);
    }
    let key = e.name.to_ascii_lowercase();
    if HEAD_BOUND.contains(&p.as_str()) {
        match &ctx.open_pr_refs {
            Source::Unknown => return Some(Reason::OpenPrUnknown),
            Source::Known(refs) if refs.contains(&key) => return Some(Reason::OpenPrRef),
            Source::Known(_) => {}
        }
        match &ctx.heads {
            Source::Known(m) => match m.get(&key) {
                Some(HeadState::Settled) => {}
                Some(HeadState::Open) => return Some(Reason::HeadOpen),
                None => return Some(Reason::HeadUnknown),
            },
            Source::Unknown => return Some(Reason::HeadUnknown),
        }
    }
    table
        .matches(&e.name, false)
        .map(|pat| Reason::Table(pat.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn file(name: &str) -> Entry {
        Entry {
            name: name.into(),
            is_dir: false,
            mtime: 0,
            header: None,
        }
    }

    fn ctx_all_known() -> Ctx {
        Ctx {
            latest_handoff: BTreeSet::new(),
            open_pr_refs: Source::Known(BTreeSet::new()),
            heads: Source::Known(BTreeMap::new()),
        }
    }

    fn table() -> ExcludeTable {
        parse_exclude_table("- SIGN-* 、CONSENT-*\n- tools-a63ab/(leak-scan)\n").unwrap()
    }

    #[test]
    fn header_reads_keys_comments_unknown_keys_and_refuses_an_unclosed_block() {
        let h = parse_header("---\nstatus: 定稿 # r2 定了\nrev: r2\nweird: x: y\n---\n# body\n")
            .unwrap()
            .unwrap();
        assert_eq!(h.get("status"), Some("定稿"));
        assert_eq!(h.get("rev"), Some("r2"));
        assert_eq!(h.get("weird"), Some("x: y"), "unknown keys are kept");
        assert_eq!(parse_header("# 標題\n---\n"), Ok(None), "no header");
        assert_eq!(
            parse_header("---\nstatus: 定稿\n"),
            Err(HeaderError::Unclosed)
        );
        assert!(parse_header("\u{feff}---\nrev: r1\n---\n")
            .unwrap()
            .is_some());
    }

    #[test]
    fn kind_comes_from_the_name_prefix_and_unknown_is_not_guessed() {
        assert_eq!(
            classify("PLAN-s81-p1-aib-pending-b433b-1114.md"),
            Kind::Evolving("PLAN".into())
        );
        assert_eq!(
            classify("receipt-pro13-x.out"),
            Kind::Record("RECEIPT".into())
        );
        assert_eq!(classify("sg-sheet.png"), Kind::Unknown);
        assert_eq!(classify("MERGE-QUEUE.md"), Kind::Unknown);
    }

    #[test]
    fn the_table_reads_paths_drops_notes_and_an_empty_table_is_an_error() {
        let t = parse_exclude_table(
            "# 標題\n- aib2-a63ab-1107/(進度樹 cabin-plan、看板 board、RULES-seats-aib2.md)\n\
             - SIGN-* 、CONSENT-RUN-* 、CONSENT-*(Andy 指紋)\n\
             - **每席最新一份 HANDOFF-<席>-*.md 不能搬**(鉤子)\n\
             - 可以搬\u{ff08}他自己說的\u{ff09}\u{ff1a}tools-a9136/(空)\n",
        )
        .unwrap();
        assert_eq!(
            t.patterns,
            vec![
                "aib2-a63ab-1107/",
                "SIGN-*",
                "CONSENT-RUN-*",
                "CONSENT-*",
                "tools-a9136/"
            ]
        );
        assert_eq!(t.matches("sign-old-f1fae.sh", false), Some("SIGN-*"));
        assert_eq!(t.matches("aib2-a63ab-1107", true), Some("aib2-a63ab-1107/"));
        assert_eq!(
            t.matches("aib2-a63ab-1107", false),
            None,
            "folder pattern, file name"
        );
        assert_eq!(parse_exclude_table("# 沒有條目\n"), Err(TableError::Empty));
        assert_eq!(parse_exclude_table(""), Err(TableError::Empty));
    }

    #[test]
    fn glob_is_case_blind_and_star_matches_any_run() {
        assert!(glob("TEST-*-043dd-*-lin.txt", "test-s76-043dd-abc-lin.txt"));
        assert!(!glob("TEST-*-043dd-*-lin.txt", "TEST-s76-043dd-abc.txt"));
        assert!(glob("PUSH-FREEZE*", "PUSH-FREEZE"));
        assert!(!glob("SIGN-*", "XSIGN-1"));
    }

    #[test]
    fn subfolders_push_freeze_and_own_files_are_never_moved() {
        let (t, c) = (table(), ctx_all_known());
        let mut d = file("rx-old-a9136");
        d.is_dir = true;
        assert_eq!(excluded(&d, &t, &c), Some(Reason::Subfolder));
        assert_eq!(
            excluded(&file("PUSH-FREEZE-macos"), &t, &c),
            Some(Reason::PushFreeze)
        );
        assert_eq!(excluded(&file(EXCLUDE_FILE), &t, &c), Some(Reason::OwnFile));
        assert_eq!(excluded(&file("index.json"), &t, &c), Some(Reason::OwnFile));
        assert_eq!(excluded(&file("OPINION-S81-b433b-1114.md"), &t, &c), None);
    }

    #[test]
    fn a_resident_header_and_the_table_keep_a_file() {
        let (t, c) = (table(), ctx_all_known());
        let mut e = file("NOTES-keep.md");
        e.header = parse_header("---\nstatus: 常駐\n---\n").unwrap();
        assert_eq!(excluded(&e, &t, &c), Some(Reason::Resident));
        assert_eq!(
            excluded(&file("CONSENT-RUN-x.txt"), &t, &c),
            Some(Reason::Table("CONSENT-*".into()))
        );
    }

    #[test]
    fn each_seats_latest_handoff_is_kept_and_older_ones_are_records() {
        let mut a = file("HANDOFF-b433b-1113-1033.md");
        a.mtime = 100;
        let mut b = file("HANDOFF-b433b-1114-0910.md");
        b.mtime = 200;
        let mut c = file("HANDOFF-f1fae-1112-2200.md");
        c.mtime = 50;
        let all = [a.clone(), b.clone(), c.clone(), file("HANDOFF-notes.md")];
        let latest = latest_handoffs(&all);
        assert_eq!(
            latest,
            BTreeSet::from([b.name.clone(), c.name.clone()]),
            "one per seat, by mtime"
        );
        let ctx = Ctx {
            latest_handoff: latest,
            ..ctx_all_known()
        };
        let t = table();
        assert_eq!(excluded(&b, &t, &ctx), Some(Reason::LatestHandoff));
        assert_eq!(excluded(&a, &t, &ctx), None, "an older handoff is a record");
    }

    #[test]
    fn a_file_an_open_pr_reads_is_kept_and_an_unknown_pr_list_keeps_everything_head_bound() {
        let t = table();
        let r = file("RECEIPT-pro13-n2a-b433b-7a63ab912.out");
        let key = r.name.to_ascii_lowercase();
        let reading = Ctx {
            open_pr_refs: Source::Known(BTreeSet::from([key.clone()])),
            heads: Source::Known(BTreeMap::from([(key.clone(), HeadState::Settled)])),
            ..ctx_all_known()
        };
        assert_eq!(excluded(&r, &t, &reading), Some(Reason::OpenPrRef));
        let unknown = Ctx {
            open_pr_refs: Source::Unknown,
            ..reading.clone()
        };
        assert_eq!(excluded(&r, &t, &unknown), Some(Reason::OpenPrUnknown));
        let free = Ctx {
            open_pr_refs: Source::Known(BTreeSet::new()),
            ..reading
        };
        assert_eq!(excluded(&r, &t, &free), None, "PR merged, head in main");
    }

    #[test]
    fn a_review_whose_head_is_not_in_main_or_unknown_is_kept() {
        let t = table();
        let r = file("REVIEW-n2a-plan-gate-1ced7-08620f19.md");
        let key = r.name.to_ascii_lowercase();
        let with = |s: Source<BTreeMap<String, HeadState>>| Ctx {
            heads: s,
            ..ctx_all_known()
        };
        let open = with(Source::Known(BTreeMap::from([(
            key.clone(),
            HeadState::Open,
        )])));
        assert_eq!(excluded(&r, &t, &open), Some(Reason::HeadOpen));
        assert_eq!(
            excluded(&r, &t, &with(Source::Known(BTreeMap::new()))),
            Some(Reason::HeadUnknown),
            "not listed = not known"
        );
        assert_eq!(
            excluded(&r, &t, &with(Source::Unknown)),
            Some(Reason::HeadUnknown)
        );
        let settled = with(Source::Known(BTreeMap::from([(key, HeadState::Settled)])));
        assert_eq!(excluded(&r, &t, &settled), None);
        // not head-bound: the head source does not matter
        assert_eq!(
            excluded(
                &file("OPINION-S81-b433b-1114.md"),
                &t,
                &with(Source::Unknown)
            ),
            None
        );
    }
}
