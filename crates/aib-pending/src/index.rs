//! S81 p-1b(PLAN-s81-p1-aib-pending-b433b-1114 r2 §指令):索引、引用掃描、取代鏈。
//!
//! 這裡只算、不碰檔案系統:呼叫端(main.rs)讀好頂層檔交進來。
//! - 引用:文字裡出現的完整檔名或 `d_xxxxxx` id(檔名不分大小寫;自己引用自己不算)。
//! - 被誰取代:反推檔頭 `supersedes:` 裡寫到的檔名 / id。
//! - 只掃 pending 頂層;記憶夾、技能夾沒掃(p-4 搬檔前要擴大,印在輸出裡提醒)。

use crate::{classify, Entry, HeaderRead, Kind};
use std::collections::{BTreeMap, BTreeSet};

/// 超過就不掃引用(檔頭照讀),列進 skipped_large
pub const MAX_SCAN_BYTES: u64 = 2 * 1024 * 1024;
/// 輸出裡的提醒(PLAN 第 4 題 L1)
pub const SCOPE_NOTE: &str = "只掃了 pending 頂層;記憶夾、技能夾、子資料夾沒掃";
/// changes 每一類最多列幾個名字(其餘只計數)
pub const CHANGES_CAP: usize = 200;

/// 一個頂層檔(呼叫端讀好)
#[derive(Debug, Clone)]
pub struct ScanFile {
    pub entry: Entry,
    pub size: u64,
    /// 要掃引用的文字;太大 / 二進位 / 讀不到 = None
    pub text: Option<String>,
}

/// `d_` + 6 碼小寫 base32(a–z、2–7)
pub fn is_doc_id(t: &str) -> bool {
    t.strip_prefix("d_").is_some_and(|r| {
        r.len() == 6
            && r.bytes()
                .all(|b| b.is_ascii_lowercase() || (b'2'..=b'7').contains(&b))
    })
}

fn name_char(c: char) -> bool {
    c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-')
}

/// 一段文字裡像檔名 / id 的字(句尾的 `.` `-` 去掉後也試)
fn tokens(text: &str) -> impl Iterator<Item = &str> {
    text.split(|c: char| !name_char(c))
        .filter(|t| !t.is_empty())
        .flat_map(|t| {
            let trimmed = t.trim_end_matches(['.', '-']);
            let second = (trimmed != t && !trimmed.is_empty()).then_some(trimmed);
            std::iter::once(t).chain(second)
        })
}

/// 名字 / id → 檔名(小寫鍵)
#[derive(Debug, Default)]
struct Names {
    by_lower: BTreeMap<String, String>,
    by_id: BTreeMap<String, String>,
}

impl Names {
    fn of(files: &[ScanFile]) -> Self {
        let mut n = Names::default();
        for f in files {
            n.by_lower
                .insert(f.entry.name.to_ascii_lowercase(), f.entry.name.clone());
            if let Some(id) = header_id(&f.entry.header) {
                n.by_id.insert(id, f.entry.name.clone());
            }
        }
        n
    }

    fn resolve(&self, t: &str) -> Option<&String> {
        if is_doc_id(t) {
            return self.by_id.get(t);
        }
        self.by_lower.get(&t.to_ascii_lowercase())
    }
}

fn header_get<'a>(h: &'a HeaderRead, key: &str) -> Option<&'a str> {
    match h {
        HeaderRead::Present(h) => h.get(key).filter(|v| !v.is_empty()),
        _ => None,
    }
}

fn header_id(h: &HeaderRead) -> Option<String> {
    header_get(h, "id")
        .filter(|v| is_doc_id(v))
        .map(str::to_string)
}

/// 索引的一列
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Row {
    pub name: String,
    /// evolving / record / unknown
    pub kind: &'static str,
    pub prefix: String,
    /// present / absent / unreadable
    pub header: &'static str,
    pub id: Option<String>,
    pub status: Option<String>,
    pub rev: Option<String>,
    pub supersedes: Option<String>,
    pub node: Option<String>,
    pub owner: Option<String>,
    pub mtime: u64,
    pub size: u64,
    /// 有幾個別的檔引用它
    pub refs: usize,
    /// 檔頭 supersedes 寫到它的檔(反推)
    pub superseded_by: Vec<String>,
}

/// 這次和上一份 INDEX.json 的差異(只留最近一次)
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Changes {
    pub added: usize,
    pub removed: usize,
    pub status_changed: usize,
    /// 前 CHANGES_CAP 個
    pub added_names: Vec<String>,
    pub removed_names: Vec<String>,
    /// (檔名, 舊狀態, 新狀態)
    pub status_names: Vec<(String, String, String)>,
}

/// 上一份 INDEX.json 讀出來的 檔名 → 狀態(沒寫狀態 = 空字串)
pub type Previous = BTreeMap<String, String>;

pub fn diff(prev: &Previous, rows: &[Row]) -> Changes {
    let mut c = Changes::default();
    let now: BTreeMap<&str, &str> = rows
        .iter()
        .map(|r| (r.name.as_str(), r.status.as_deref().unwrap_or("")))
        .collect();
    for (name, status) in &now {
        match prev.get(*name) {
            None => {
                c.added += 1;
                if c.added_names.len() < CHANGES_CAP {
                    c.added_names.push((*name).to_string());
                }
            }
            Some(old) if old != status => {
                c.status_changed += 1;
                if c.status_names.len() < CHANGES_CAP {
                    c.status_names
                        .push(((*name).to_string(), old.clone(), (*status).to_string()));
                }
            }
            Some(_) => {}
        }
    }
    for name in prev.keys() {
        if !now.contains_key(name.as_str()) {
            c.removed += 1;
            if c.removed_names.len() < CHANGES_CAP {
                c.removed_names.push(name.clone());
            }
        }
    }
    c
}

/// 每個檔被哪些別的檔引用(去重:一個檔引用很多次只算一次;自己不算)
fn referrers(files: &[ScanFile], names: &Names) -> BTreeMap<String, BTreeSet<String>> {
    let mut out: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for f in files {
        let Some(text) = &f.text else { continue };
        for t in tokens(text) {
            if let Some(target) = names.resolve(t) {
                if *target != f.entry.name {
                    out.entry(target.clone())
                        .or_default()
                        .insert(f.entry.name.clone());
                }
            }
        }
    }
    out
}

/// supersedes 反推:被取代的檔 → 取代它的檔
fn superseders(files: &[ScanFile], names: &Names) -> BTreeMap<String, BTreeSet<String>> {
    let mut out: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for f in files {
        let Some(v) = header_get(&f.entry.header, "supersedes") else {
            continue;
        };
        for t in tokens(v) {
            if let Some(old) = names.resolve(t) {
                if *old != f.entry.name {
                    out.entry(old.clone())
                        .or_default()
                        .insert(f.entry.name.clone());
                }
            }
        }
    }
    out
}

/// 掃好的頂層檔 → 索引列(依檔名排序)。with_refs = false 時 refs 一律 0(list 用,不掃全文)
pub fn rows(files: &[ScanFile], with_refs: bool) -> Vec<Row> {
    let names = Names::of(files);
    let refs = if with_refs {
        referrers(files, &names)
    } else {
        BTreeMap::new()
    };
    let sup = superseders(files, &names);
    let mut rows: Vec<Row> = files
        .iter()
        .map(|f| {
            let e = &f.entry;
            let (kind, prefix) = match classify(&e.name) {
                Kind::Evolving(p) => ("evolving", p),
                Kind::Record(p) => ("record", p),
                Kind::Unknown => ("unknown", crate::prefix(&e.name)),
            };
            let get = |k: &str| header_get(&e.header, k).map(str::to_string);
            Row {
                name: e.name.clone(),
                kind,
                prefix,
                header: match e.header {
                    HeaderRead::Present(_) => "present",
                    HeaderRead::Absent => "absent",
                    HeaderRead::Unreadable(_) => "unreadable",
                },
                id: header_id(&e.header),
                status: get("status"),
                rev: get("rev"),
                supersedes: get("supersedes"),
                node: get("node"),
                owner: get("owner"),
                mtime: e.mtime,
                size: f.size,
                refs: refs.get(&e.name).map_or(0, BTreeSet::len),
                superseded_by: sup
                    .get(&e.name)
                    .map(|s| s.iter().cloned().collect())
                    .unwrap_or_default(),
            }
        })
        .collect();
    rows.sort_by(|a, b| a.name.cmp(&b.name));
    rows
}

/// 某一檔被引用的位置:(引用它的檔, 行號 1 起)。show 用。
pub fn refs_to(files: &[ScanFile], target: &str) -> Vec<(String, usize)> {
    let names = Names::of(files);
    let mut out = Vec::new();
    for f in files {
        if f.entry.name == target {
            continue;
        }
        let Some(text) = &f.text else { continue };
        for (i, line) in text.lines().enumerate() {
            if tokens(line).any(|t| names.resolve(t).is_some_and(|n| n == target)) {
                out.push((f.entry.name.clone(), i + 1));
            }
        }
    }
    out
}

/// 名字或 id 找檔(名字不分大小寫)
pub fn find<'a>(rows: &'a [Row], key: &str) -> Option<&'a Row> {
    if is_doc_id(key) {
        return rows.iter().find(|r| r.id.as_deref() == Some(key));
    }
    rows.iter().find(|r| r.name.eq_ignore_ascii_case(key))
}

/// 取代鏈:往前(它取代的,沿 supersedes 第一個認得的檔名)與往後(取代它的,沿 superseded_by 第一個)。
/// 有環就停(每個檔只走一次)。
pub fn chain<'a>(rows: &'a [Row], start: &'a Row) -> (Vec<&'a str>, Vec<&'a str>) {
    let by_name: BTreeMap<String, &Row> = rows
        .iter()
        .map(|r| (r.name.to_ascii_lowercase(), r))
        .collect();
    let by_id: BTreeMap<&str, &Row> = rows
        .iter()
        .filter_map(|r| r.id.as_deref().map(|i| (i, r)))
        .collect();
    let mut seen = BTreeSet::from([start.name.as_str()]);
    let mut older = Vec::new();
    let mut cur = start;
    while let Some(next) = cur.supersedes.as_deref().and_then(|v| {
        tokens(v).find_map(|t| {
            if is_doc_id(t) {
                by_id.get(t).copied()
            } else {
                by_name.get(&t.to_ascii_lowercase()).copied()
            }
        })
    }) {
        if !seen.insert(next.name.as_str()) {
            break;
        }
        older.push(next.name.as_str());
        cur = next;
    }
    let mut newer = Vec::new();
    cur = start;
    while let Some(next) = cur
        .superseded_by
        .first()
        .and_then(|n| by_name.get(&n.to_ascii_lowercase()).copied())
    {
        if !seen.insert(next.name.as_str()) {
            break;
        }
        newer.push(next.name.as_str());
        cur = next;
    }
    (older, newer)
}

/// 掃描前後的 (檔名 → (mtime, 大小)) 比一次:不一樣的檔名(前 CHANGES_CAP 個)。空 = 掃描中沒變動。
pub fn moved_during_scan(
    before: &BTreeMap<String, (u64, u64)>,
    after: &BTreeMap<String, (u64, u64)>,
) -> Vec<String> {
    let mut out: Vec<String> = before
        .iter()
        .filter(|(k, v)| after.get(*k) != Some(v))
        .map(|(k, _)| k.clone())
        .chain(after.keys().filter(|k| !before.contains_key(*k)).cloned())
        .collect();
    out.sort();
    out.dedup();
    out.truncate(CHANGES_CAP);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sf(name: &str, header: &str, text: &str) -> ScanFile {
        ScanFile {
            entry: Entry {
                name: name.into(),
                is_dir: false,
                mtime: 7,
                header: HeaderRead::of(Ok(header)),
            },
            size: text.len() as u64,
            text: Some(text.into()),
        }
    }

    #[test]
    fn ids_are_d_and_six_lowercase_base32() {
        assert!(is_doc_id("d_ab2c7z"));
        assert!(!is_doc_id("d_ab2c71"), "1 is not base32");
        assert!(!is_doc_id("d_ABCDEF"));
        assert!(!is_doc_id("d_abcde"));
        assert!(!is_doc_id("x_abcdef"));
    }

    #[test]
    fn refs_count_other_files_once_by_name_or_id_and_never_the_file_itself() {
        let files = vec![
            sf(
                "PLAN-a-b433b-1114.md",
                "---\nid: d_aaaaaa\nstatus: 定稿\n---\n",
                "---\nid: d_aaaaaa\n---\n自己 PLAN-a-b433b-1114.md 不算\n",
            ),
            sf(
                "REVIEW-x.md",
                "",
                "看 pending/PLAN-a-b433b-1114.md。再看一次 plan-a-b433b-1114.md\n",
            ),
            sf("DEV-y.md", "", "照 d_aaaaaa 做;REVIEW-x.md\n"),
            sf("RELAY-z.txt", "", "沒有引用 PLAN-a-b433b-1114\n"),
        ];
        let r = rows(&files, true);
        let plan = find(&r, "PLAN-a-b433b-1114.md").unwrap();
        assert_eq!(plan.refs, 2, "REVIEW-x (twice, counted once) + DEV-y by id");
        assert_eq!(plan.id.as_deref(), Some("d_aaaaaa"));
        assert_eq!(find(&r, "REVIEW-x.md").unwrap().refs, 1);
        assert_eq!(find(&r, "d_aaaaaa").unwrap().name, "PLAN-a-b433b-1114.md");
        assert_eq!(
            refs_to(&files, "PLAN-a-b433b-1114.md"),
            vec![("REVIEW-x.md".to_string(), 1), ("DEV-y.md".to_string(), 1)]
        );
        let no_refs = rows(&files, false);
        assert_eq!(find(&no_refs, "PLAN-a-b433b-1114.md").unwrap().refs, 0);
    }

    #[test]
    fn a_file_with_no_text_is_not_scanned_but_still_indexed() {
        let mut big = sf("RECEIPT-big.out", "", "PLAN-a.md");
        big.text = None;
        let files = vec![sf("PLAN-a.md", "", ""), big];
        let r = rows(&files, true);
        assert_eq!(find(&r, "PLAN-a.md").unwrap().refs, 0);
        assert!(find(&r, "RECEIPT-big.out").is_some());
    }

    #[test]
    fn superseded_by_is_read_back_from_supersedes_and_the_chain_walks_both_ways() {
        let files = vec![
            sf("PLAN-p-r0.md", "---\nstatus: 已棄用\n---\n", ""),
            sf(
                "PLAN-p-r1.md",
                "---\nstatus: 已棄用\nsupersedes: PLAN-p-r0.md(r0)\n---\n",
                "",
            ),
            sf(
                "PLAN-p-r2.md",
                "---\nstatus: 定稿\nid: d_bbbbbb\nsupersedes: plan-p-r1.md\n---\n",
                "",
            ),
            sf("DEV-q.md", "---\nsupersedes: d_bbbbbb\n---\n", ""),
        ];
        let r = rows(&files, false);
        assert_eq!(
            find(&r, "PLAN-p-r0.md").unwrap().superseded_by,
            vec!["PLAN-p-r1.md"]
        );
        assert_eq!(
            find(&r, "PLAN-p-r2.md").unwrap().superseded_by,
            vec!["DEV-q.md"],
            "by id"
        );
        let mid = find(&r, "PLAN-p-r1.md").unwrap();
        assert_eq!(
            chain(&r, mid),
            (vec!["PLAN-p-r0.md"], vec!["PLAN-p-r2.md", "DEV-q.md"])
        );
    }

    #[test]
    fn a_supersedes_loop_stops() {
        let files = vec![
            sf("PLAN-a.md", "---\nsupersedes: PLAN-b.md\n---\n", ""),
            sf("PLAN-b.md", "---\nsupersedes: PLAN-a.md\n---\n", ""),
        ];
        let r = rows(&files, false);
        let a = find(&r, "PLAN-a.md").unwrap();
        assert_eq!(chain(&r, a), (vec!["PLAN-b.md"], vec![]));
    }

    #[test]
    fn rows_carry_kind_header_state_and_fields() {
        let files = vec![
            sf(
                "DEV-S81-x.md",
                "---\nstatus: 定稿\nrev: r2\nnode: S81\nowner: b433b\n---\n",
                "",
            ),
            sf("WEIRD-x.md", "---\nstatus: x\n", ""),
            sf("RELAY-dm-1.txt", "", ""),
        ];
        let r = rows(&files, false);
        let d = find(&r, "DEV-S81-x.md").unwrap();
        assert_eq!(
            (
                d.kind,
                d.header,
                d.rev.as_deref(),
                d.node.as_deref(),
                d.owner.as_deref()
            ),
            (
                "evolving",
                "present",
                Some("r2"),
                Some("S81"),
                Some("b433b")
            )
        );
        let w = find(&r, "WEIRD-x.md").unwrap();
        assert_eq!(
            (w.kind, w.header, w.prefix.as_str()),
            ("unknown", "unreadable", "WEIRD")
        );
        assert_eq!(find(&r, "RELAY-dm-1.txt").unwrap().kind, "record");
    }

    #[test]
    fn changes_list_added_removed_and_status_changed() {
        let files = vec![
            sf("PLAN-a.md", "---\nstatus: 定稿\n---\n", ""),
            sf("PLAN-b.md", "", ""),
        ];
        let r = rows(&files, false);
        let prev = Previous::from([
            ("PLAN-a.md".to_string(), "草稿".to_string()),
            ("PLAN-gone.md".to_string(), String::new()),
        ]);
        let c = diff(&prev, &r);
        assert_eq!((c.added, c.removed, c.status_changed), (1, 1, 1));
        assert_eq!(c.added_names, vec!["PLAN-b.md"]);
        assert_eq!(c.removed_names, vec!["PLAN-gone.md"]);
        assert_eq!(
            c.status_names,
            vec![("PLAN-a.md".into(), "草稿".into(), "定稿".into())]
        );
        assert_eq!(diff(&Previous::new(), &r).added, 2);
    }

    #[test]
    fn a_file_that_changed_or_appeared_during_the_scan_marks_it_partial() {
        let before = BTreeMap::from([
            ("A".to_string(), (1, 10)),
            ("B".to_string(), (1, 10)),
            ("C".to_string(), (1, 10)),
        ]);
        let mut after = before.clone();
        assert!(moved_during_scan(&before, &after).is_empty());
        after.insert("B".into(), (2, 10));
        after.remove("C");
        after.insert("D".into(), (1, 1));
        assert_eq!(moved_during_scan(&before, &after), vec!["B", "C", "D"]);
    }
}
