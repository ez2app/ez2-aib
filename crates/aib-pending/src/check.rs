//! S81 p-1c(PLAN-s81-p1-aib-pending-b433b-1114 r2 §1.4):`check` 的判斷與 `new` 的檔頭。
//!
//! check 只報不擋;這裡只算、不碰檔案系統。new 只建新檔,檔頭在這裡組。

use crate::index::{is_doc_id, Row};
use crate::{ExcludeTable, EVOLVING};
use std::collections::BTreeMap;
use std::fmt;

/// 一條報告
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Finding {
    /// 會演進的文件沒有檔頭
    EvolvingNoHeader(String),
    /// 檔頭讀不到(沒結尾 `---` / 不是 UTF-8 / 讀檔失敗)
    HeaderUnreadable(String),
    /// 已棄用,沒有人取代它,檔頭也沒寫理由(`why:`)
    DeprecatedOrphan(String),
    /// 同一節點、同一種會演進文件有兩份以上定稿:(節點, 開頭, 檔名們)
    TwoFinals(String, String, Vec<String>),
    /// supersedes 寫到的檔名 / id 在 pending 頂層找不到:(檔名, 找不到的字)
    MissingSupersedes(String, String),
    /// 兩份以上檔頭寫同一個 id:(id, 檔名們)(1ced7 p-1b N1)
    DuplicateId(String, Vec<String>),
    /// 檔頭寫 status: 常駐,排除表沒有它(也沒有程式規則保護)
    ResidentNotInTable(String),
    /// 不認得的開頭:(開頭, 幾個檔, 前 3 個檔名)
    UnknownPrefix(String, usize, Vec<String>),
}

impl fmt::Display for Finding {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Finding::EvolvingNoHeader(n) => write!(f, "會演進沒檔頭\t{n}"),
            Finding::HeaderUnreadable(n) => write!(f, "檔頭讀不到\t{n}"),
            Finding::DeprecatedOrphan(n) => write!(f, "已棄用沒人取代也沒理由\t{n}"),
            Finding::TwoFinals(node, p, ns) => {
                write!(f, "同節點兩份定稿\t{node} {p}\t{}", ns.join(" "))
            }
            Finding::MissingSupersedes(n, t) => write!(f, "supersedes 找不到\t{n}\t{t}"),
            Finding::DuplicateId(id, ns) => write!(f, "id 重複\t{id}\t{}", ns.join(" ")),
            Finding::ResidentNotInTable(n) => write!(f, "常駐不在排除表\t{n}"),
            Finding::UnknownPrefix(p, c, ns) => {
                write!(f, "不認得的開頭\t{p}\t{c} 個\t{}", ns.join(" "))
            }
        }
    }
}

/// supersedes 值裡「看起來是指一個檔」的字:d_ id,或帶副檔名的檔名
fn names_in(v: &str) -> impl Iterator<Item = &str> {
    v.split(|c: char| !(c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-')))
        .map(|t| t.trim_end_matches(['.', '-']))
        .filter(|t| {
            is_doc_id(t)
                || [".md", ".txt", ".json", ".out", ".diff", ".jsonl"]
                    .iter()
                    .any(|ext| t.to_ascii_lowercase().ends_with(ext) && t.len() > ext.len())
        })
}

/// 檔頭有沒有寫理由(棄用不需要取代者時)
pub const WHY_KEYS: &[&str] = &["why", "理由"];

/// 檢查索引列。`table` = None 表示排除表讀不到(呼叫端另回 rc 3),常駐那條就不報。
/// `why` 是每個檔頭有沒有寫 WHY_KEYS 其中之一(檔名 → 有沒有);Row 沒帶任意鍵,所以另給。
pub fn check(
    rows: &[Row],
    table: Option<&ExcludeTable>,
    why: &BTreeMap<String, bool>,
) -> Vec<Finding> {
    let mut out = Vec::new();
    let lower: BTreeMap<String, &Row> = rows
        .iter()
        .map(|r| (r.name.to_ascii_lowercase(), r))
        .collect();
    let mut ids: BTreeMap<&str, Vec<String>> = BTreeMap::new();
    let mut finals: BTreeMap<(String, String), Vec<String>> = BTreeMap::new();
    let mut unknown: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for r in rows {
        match r.header {
            "unreadable" => out.push(Finding::HeaderUnreadable(r.name.clone())),
            "absent" if r.kind == "evolving" => out.push(Finding::EvolvingNoHeader(r.name.clone())),
            _ => {}
        }
        let status = r.status.as_deref().unwrap_or("");
        if status == "已棄用"
            && r.superseded_by.is_empty()
            && !why.get(&r.name).copied().unwrap_or(false)
        {
            out.push(Finding::DeprecatedOrphan(r.name.clone()));
        }
        if status == "定稿" && EVOLVING.contains(&r.prefix.as_str()) {
            if let Some(node) = &r.node {
                finals
                    .entry((node.clone(), r.prefix.clone()))
                    .or_default()
                    .push(r.name.clone());
            }
        }
        if let Some(v) = &r.supersedes {
            for t in names_in(v) {
                let found = if is_doc_id(t) {
                    rows.iter().any(|x| x.id.as_deref() == Some(t))
                } else {
                    lower.contains_key(&t.to_ascii_lowercase())
                };
                if !found {
                    out.push(Finding::MissingSupersedes(r.name.clone(), t.to_string()));
                }
            }
        }
        if let Some(id) = &r.id {
            ids.entry(id.as_str()).or_default().push(r.name.clone());
        }
        if status == "常駐" {
            if let Some(t) = table {
                if t.matches(&r.name, false).is_none() {
                    out.push(Finding::ResidentNotInTable(r.name.clone()));
                }
            }
        }
        if r.kind == "unknown" {
            unknown
                .entry(r.prefix.clone())
                .or_default()
                .push(r.name.clone());
        }
    }
    for ((node, p), names) in finals {
        if names.len() > 1 {
            out.push(Finding::TwoFinals(node, p, names));
        }
    }
    for (id, names) in ids {
        if names.len() > 1 {
            out.push(Finding::DuplicateId(id.to_string(), names));
        }
    }
    for (p, names) in unknown {
        let n = names.len();
        out.push(Finding::UnknownPrefix(
            p,
            n,
            names.into_iter().take(3).collect(),
        ));
    }
    out
}

const BASE32: &[u8; 32] = b"abcdefghijklmnopqrstuvwxyz234567";

/// 30 位元 → `d_` + 6 碼小寫 base32
pub fn doc_id_from(bits: u64) -> String {
    let mut s = String::from("d_");
    for i in (0..6).rev() {
        s.push(char::from(BASE32[((bits >> (i * 5)) & 31) as usize]));
    }
    s
}

/// new 的檔名檢查:只收頂層檔名(沒有路徑分隔、不是 `.` 開頭、沒有 `..`),開頭要等於 --kind 且是會演進的種類
pub fn new_name_ok(name: &str, kind: &str) -> Result<(), String> {
    if name.is_empty()
        || name.contains(['/', '\\', ':'])
        || name.starts_with('.')
        || name.contains("..")
    {
        return Err(format!("檔名只收 pending 頂層的檔名:{name}"));
    }
    let k = kind.to_ascii_uppercase();
    if !EVOLVING.contains(&k.as_str()) {
        return Err(format!(
            "--kind 要是會演進的種類({}):{kind}",
            EVOLVING.join(" ")
        ));
    }
    if crate::prefix(name) != k {
        return Err(format!("檔名開頭要是 {k}-:{name}"));
    }
    Ok(())
}

/// 欄位值不能換行、不能有 `#`(會被當註解)、不能是空的
pub fn field_ok(v: &str) -> bool {
    !v.trim().is_empty() && !v.contains(['\n', '\r', '#'])
}

/// new 寫的檔頭(status 草稿、rev r0)
pub fn new_header(id: &str, node: &str, owner: &str) -> String {
    format!("---\nid: {id}\nstatus: 草稿\nrev: r0\nnode: {node}\nowner: {owner}\n---\n")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::index::{rows, ScanFile};
    use crate::{parse_exclude_table, Entry, HeaderRead};

    fn sf(name: &str, header: &str) -> ScanFile {
        ScanFile {
            entry: Entry {
                name: name.into(),
                is_dir: false,
                mtime: 0,
                header: HeaderRead::of(Ok(header)),
            },
            size: 0,
            text: None,
        }
    }

    fn run(files: &[ScanFile], why: &[&str]) -> Vec<Finding> {
        let t = parse_exclude_table("- EXCLUDE-*\n- MERGE-QUEUE.md\n").unwrap();
        let w = why.iter().map(|n| (n.to_string(), true)).collect();
        check(&rows(files, false), Some(&t), &w)
    }

    #[test]
    fn an_evolving_file_with_no_header_and_an_unreadable_header_are_reported() {
        let f = run(
            &[
                sf("PLAN-a.md", ""),
                sf("RELAY-x.txt", ""),
                sf("DEV-b.md", "---\nstatus: 定稿\n"),
            ],
            &[],
        );
        assert!(
            f.contains(&Finding::EvolvingNoHeader("PLAN-a.md".into())),
            "{f:?}"
        );
        assert!(
            f.contains(&Finding::HeaderUnreadable("DEV-b.md".into())),
            "{f:?}"
        );
        assert!(
            !f.iter().any(|x| format!("{x}").contains("RELAY-x.txt")),
            "{f:?}"
        );
    }

    #[test]
    fn deprecated_needs_a_successor_or_a_reason() {
        let f = run(
            &[
                sf("PLAN-old.md", "---\nstatus: 已棄用\n---\n"),
                sf("PLAN-old2.md", "---\nstatus: 已棄用\n---\n"),
                sf(
                    "PLAN-new.md",
                    "---\nstatus: 定稿\nsupersedes: PLAN-old2.md\n---\n",
                ),
                sf("PLAN-gone.md", "---\nstatus: 已棄用\nwhy: 不做了\n---\n"),
            ],
            &["PLAN-gone.md"],
        );
        let orphans: Vec<_> = f
            .iter()
            .filter(|x| matches!(x, Finding::DeprecatedOrphan(_)))
            .collect();
        assert_eq!(
            orphans,
            vec![&Finding::DeprecatedOrphan("PLAN-old.md".into())]
        );
    }

    #[test]
    fn two_finals_of_one_kind_on_one_node_are_reported_but_reviews_are_not() {
        let f = run(
            &[
                sf("DEV-a.md", "---\nstatus: 定稿\nnode: S81\n---\n"),
                sf("DEV-b.md", "---\nstatus: 定稿\nnode: S81\n---\n"),
                sf("PLAN-c.md", "---\nstatus: 定稿\nnode: S81\n---\n"),
                sf("REVIEW-x.md", "---\nstatus: 定稿\nnode: S81\n---\n"),
                sf("REVIEW-y.md", "---\nstatus: 定稿\nnode: S81\n---\n"),
            ],
            &[],
        );
        let two: Vec<_> = f
            .iter()
            .filter(|x| matches!(x, Finding::TwoFinals(..)))
            .collect();
        assert_eq!(
            two,
            vec![&Finding::TwoFinals(
                "S81".into(),
                "DEV".into(),
                vec!["DEV-a.md".into(), "DEV-b.md".into()]
            )]
        );
    }

    #[test]
    fn a_supersedes_that_names_a_missing_file_or_id_is_reported() {
        let f = run(
            &[
                sf("PLAN-a.md", "---\nid: d_aaaaaa\n---\n"),
                sf(
                    "PLAN-b.md",
                    "---\nsupersedes: PLAN-a.md、d_aaaaaa、PLAN-gone.md(r1)、d_zzzzzz、r0\n---\n",
                ),
            ],
            &[],
        );
        let miss: Vec<_> = f
            .iter()
            .filter(|x| matches!(x, Finding::MissingSupersedes(..)))
            .collect();
        assert_eq!(
            miss,
            vec![
                &Finding::MissingSupersedes("PLAN-b.md".into(), "PLAN-gone.md".into()),
                &Finding::MissingSupersedes("PLAN-b.md".into(), "d_zzzzzz".into()),
            ]
        );
    }

    #[test]
    fn a_duplicate_id_is_reported() {
        let f = run(
            &[
                sf("PLAN-a.md", "---\nid: d_aaaaaa\n---\n"),
                sf("DEV-b.md", "---\nid: d_aaaaaa\n---\n"),
                sf("DEV-c.md", "---\nid: d_cccccc\n---\n"),
            ],
            &[],
        );
        assert!(
            f.contains(&Finding::DuplicateId(
                "d_aaaaaa".into(),
                vec!["DEV-b.md".into(), "PLAN-a.md".into()]
            )),
            "{f:?}"
        );
        assert_eq!(
            f.iter()
                .filter(|x| matches!(x, Finding::DuplicateId(..)))
                .count(),
            1
        );
    }

    #[test]
    fn a_resident_file_missing_from_the_table_and_unknown_prefixes_are_reported() {
        let f = run(
            &[
                sf("MERGE-QUEUE.md", "---\nstatus: 常駐\n---\n"),
                sf("WAKE-f1fae.md", "---\nstatus: 常駐\n---\n"),
                sf("WAKE-a.md", ""),
            ],
            &[],
        );
        assert!(
            f.contains(&Finding::ResidentNotInTable("WAKE-f1fae.md".into())),
            "{f:?}"
        );
        assert!(!f.contains(&Finding::ResidentNotInTable("MERGE-QUEUE.md".into())));
        assert!(
            f.contains(&Finding::UnknownPrefix(
                "WAKE".into(),
                2,
                vec!["WAKE-a.md".into(), "WAKE-f1fae.md".into()]
            )),
            "{f:?}"
        );
        let none = check(
            &rows(&[sf("WAKE-f1fae.md", "---\nstatus: 常駐\n---\n")], false),
            None,
            &BTreeMap::new(),
        );
        assert!(!none
            .iter()
            .any(|x| matches!(x, Finding::ResidentNotInTable(_))));
    }

    #[test]
    fn ids_are_six_base32_letters_and_new_only_takes_a_top_level_evolving_name() {
        assert_eq!(doc_id_from(0), "d_aaaaaa");
        assert_eq!(doc_id_from((1 << 30) - 1), "d_777777");
        assert!(is_doc_id(&doc_id_from(0x1234_5678)));
        assert!(new_name_ok("DEV-S81-x-519d0-1115.md", "dev").is_ok());
        assert!(
            new_name_ok("DEV-x.md", "PLAN").is_err(),
            "prefix must match --kind"
        );
        assert!(
            new_name_ok("REVIEW-x.md", "REVIEW").is_err(),
            "records are not issued"
        );
        assert!(new_name_ok("sub/DEV-x.md", "DEV").is_err());
        assert!(new_name_ok("..\\DEV-x.md", "DEV").is_err());
        assert!(new_name_ok("C:DEV-x.md", "DEV").is_err());
        assert!(field_ok("S81") && !field_ok("S81 # x") && !field_ok("a\nb") && !field_ok(" "));
        assert_eq!(
            new_header("d_abcdef", "S81", "519d0"),
            "---\nid: d_abcdef\nstatus: 草稿\nrev: r0\nnode: S81\nowner: 519d0\n---\n"
        );
    }
}
