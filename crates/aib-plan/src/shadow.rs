//! 影子續跑省下的時間(Andy 11-12 23:5x「在目前目標下方再新增一個，影子續跑：共省多少時間，
//! 只要有省成功的就在下方的影子續跑項目中記錄」)。
//!
//! 資料來源 = `aib test` 每跑一批寫一列的 `verify-shadow/test-stats.jsonl`
//! (`saved_s` = 不沿用時估計要花的秒數 − 這次實際花的秒數)。
//! 只收 `saved_s` > 0 的列(省成功);0、負數、沒有數字、時間壞掉的列都不算。
//!
//! 項目名稱(Andy 11-13 00:0x「為什麼沒有帶出項目名稱」):test-stats 本身沒記測的是哪件工作，
//! 從同目錄沿用池 `verify-<repo>.rows.jsonl` 反查——那一批時間範圍內實跑的列記著工作樹路徑
//! (例 `wt.noindex/cooldown-34d15`),取最多列的那棵樹的資料夾名當名稱、尾碼 5 碼當席位。
//! 沿用池每支測試只留最新一筆，舊批次多半查不到 ⇒ 名稱留空，畫面寫「沒記到是哪件」。
//! 沿用池很大(上百 MB)⇒ 只在 test-stats 換了才掃一次，結果存快取檔。

use std::collections::{BTreeMap, HashMap};
use std::io::BufRead;
use std::path::Path;

/// 一次省成功的紀錄
#[derive(Debug, Clone, PartialEq)]
pub struct Save {
    /// UTC epoch 秒
    pub at: i64,
    pub saved_s: f64,
    /// 這批實跑幾支、沿用幾支
    pub ran: u64,
    pub reused: u64,
    /// 這批實際花的秒數(反查時間範圍用)與倉庫碼(對沿用池檔名)
    pub with_s: f64,
    pub repo: String,
    /// 測的是哪件工作(工作樹資料夾名);查不到 = None
    pub name: Option<String>,
}

impl Save {
    /// 工作樹資料夾名尾巴是 5 碼小寫十六進位 ⇒ 那一席(`cooldown-34d15` ⇒ 34d15)
    pub fn seat(&self) -> Option<&str> {
        let n = self.name.as_deref()?;
        let (_, tail) = n.rsplit_once('-')?;
        (tail.len() == 5
            && tail
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
        .then_some(tail)
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Savings {
    pub total_s: f64,
    /// 新的在前
    pub saves: Vec<Save>,
}

/// 讀不到檔 ⇒ None(畫面寫「還沒有紀錄」);讀得到但沒有省成功的列 ⇒ Some(空)
pub fn read(path: &Path) -> Option<Savings> {
    std::fs::read_to_string(path).ok().map(|t| parse(&t))
}

pub fn parse(text: &str) -> Savings {
    let mut saves: Vec<Save> = text
        .lines()
        .filter_map(|l| serde_json::from_str::<serde_json::Value>(l).ok())
        .filter_map(|v| {
            let saved_s = v.get("saved_s")?.as_f64()?;
            if !(saved_s.is_finite() && saved_s > 0.0) {
                return None;
            }
            Some(Save {
                at: parse_utc(v.get("utc")?.as_str()?)?,
                saved_s,
                ran: v.get("ran").and_then(|x| x.as_u64()).unwrap_or(0),
                reused: v.get("reused").and_then(|x| x.as_u64()).unwrap_or(0),
                with_s: v.get("with_s").and_then(|x| x.as_f64()).unwrap_or(0.0),
                repo: v
                    .get("repo")
                    .and_then(|x| x.as_str())
                    .unwrap_or("")
                    .to_string(),
                name: None,
            })
        })
        .collect();
    saves.sort_by_key(|s| std::cmp::Reverse(s.at));
    Savings {
        total_s: saves.iter().map(|s| s.saved_s).sum(),
        saves,
    }
}

/// 一行 JSON 裡 `"key": "值"` 的值(沿用池一行可能上百 KB,不整行解析;值裡沒有跳脫引號才算)
fn field<'a>(line: &'a str, key: &str) -> Option<&'a str> {
    let pat = format!("\"{key}\": \"");
    let start = line.find(&pat)? + pat.len();
    let len = line[start..].find('"')?;
    let v = &line[start..start + len];
    (!v.contains('\\')).then_some(v)
}

/// 掃沿用池，替每次省成功找出那一批測的工作樹(時間範圍 = 結束前花掉的秒數再往前 2 分、往後 1 分)
pub fn name_runs(pool_dir: &Path, s: &mut Savings) {
    // 先清掉舊名稱：重掃時沿用池不見了，不能留著上一次(可能已過時)的結果
    s.saves.iter_mut().for_each(|e| e.name = None);
    let mut runs: HashMap<String, Vec<(i64, String)>> = HashMap::new();
    for repo in s
        .saves
        .iter()
        .map(|e| e.repo.clone())
        .collect::<std::collections::BTreeSet<_>>()
    {
        if repo.is_empty() || !repo.bytes().all(|b| b.is_ascii_hexdigit()) {
            continue;
        }
        let Ok(f) = std::fs::File::open(pool_dir.join(format!("verify-{repo}.rows.jsonl"))) else {
            continue;
        };
        let rows = runs.entry(repo).or_default();
        for line in std::io::BufReader::new(f).lines().map_while(Result::ok) {
            let (Some(t), Some(p)) = (field(&line, "time_utc"), field(&line, "repo")) else {
                continue;
            };
            if let Some(at) = parse_utc(t) {
                rows.push((at, p.to_string()));
            }
        }
    }
    for e in &mut s.saves {
        let Some(rows) = runs.get(&e.repo) else {
            continue;
        };
        let lo = e.at - e.with_s.max(0.0) as i64 - 120;
        let mut count: BTreeMap<&str, usize> = BTreeMap::new();
        for (at, p) in rows {
            if (lo..=e.at + 60).contains(at) {
                *count.entry(p.as_str()).or_default() += 1;
            }
        }
        e.name = count
            .into_iter()
            .max_by_key(|(_, n)| *n)
            .and_then(|(p, _)| Path::new(p).file_name())
            .map(|n| n.to_string_lossy().into_owned());
    }
}

/// 有快取且 key 一樣(test-stats 的修改時間 + 大小)就直接套名稱;不然掃一次再寫快取
pub fn name_runs_cached(pool_dir: &Path, cache: &Path, key: &str, s: &mut Savings) {
    let cached = std::fs::read_to_string(cache)
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .filter(|v| v.get("key").and_then(|k| k.as_str()) == Some(key));
    if let Some(v) = cached {
        let names = v.get("names").and_then(|n| n.as_object());
        for e in &mut s.saves {
            e.name = names
                .and_then(|m| m.get(&e.at.to_string()))
                .and_then(|n| n.as_str())
                .map(str::to_string);
        }
        return;
    }
    name_runs(pool_dir, s);
    let names: serde_json::Map<String, serde_json::Value> = s
        .saves
        .iter()
        .filter_map(|e| Some((e.at.to_string(), e.name.clone()?.into())))
        .collect();
    let body = serde_json::json!({ "key": key, "names": names });
    if let Some(d) = cache.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    let _ = std::fs::write(cache, body.to_string());
}

/// 「2026-11-09T15:18:19Z」⇒ epoch 秒;格式不對 ⇒ None
pub fn parse_utc(s: &str) -> Option<i64> {
    let b = s.as_bytes();
    if b.len() != 20 || b[4] != b'-' || b[7] != b'-' || b[10] != b'T' || b[19] != b'Z' {
        return None;
    }
    let n = |r: std::ops::Range<usize>| s.get(r)?.parse::<i64>().ok();
    let (y, m, d) = (n(0..4)?, n(5..7)?, n(8..10)?);
    let (hh, mm, ss) = (n(11..13)?, n(14..16)?, n(17..19)?);
    if !(1..=12).contains(&m) || !(1..=31).contains(&d) || hh > 23 || mm > 59 || ss > 60 {
        return None;
    }
    Some(days_from_civil(y, m, d) * 86_400 + hh * 3600 + mm * 60 + ss)
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

/// 秒數 ⇒ 人看的長度:「17 時 45 分」「40 分 42 秒」「12 秒」(不足 1 秒算 1 秒)
pub fn dur(s: f64) -> String {
    let t = s.round().max(1.0) as u64;
    let (h, m, sec) = (t / 3600, t % 3600 / 60, t % 60);
    if h > 0 {
        format!("{h} 時 {m} 分")
    } else if m > 0 {
        format!("{m} 分 {sec} 秒")
    } else {
        format!("{sec} 秒")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_rows_that_saved_time_count_newest_first() {
        let text = r#"{"saved_s": 0.0, "ran": 3, "reused": 1, "utc": "2026-11-09T15:12:57Z"}
{"saved_s": 2442.7, "ran": 341, "reused": 0, "utc": "2026-11-06T07:53:51Z"}
not json
{"saved_s": -5, "utc": "2026-11-07T00:00:00Z"}
{"saved_s": 11.8, "ran": 2, "reused": 40, "utc": "2026-11-08T21:43:33Z"}
{"saved_s": 9.0, "utc": "bad"}
{"saved_s": null, "utc": "2026-11-08T21:43:33Z"}"#;
        let s = parse(text);
        assert_eq!(s.saves.len(), 2);
        assert_eq!(s.saves[0].saved_s, 11.8);
        assert_eq!((s.saves[0].ran, s.saves[0].reused), (2, 40));
        assert_eq!(s.saves[1].saved_s, 2442.7);
        assert!((s.total_s - 2454.5).abs() < 1e-9);
    }

    #[test]
    fn utc_parses_and_rejects_bad_shapes() {
        assert_eq!(parse_utc("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(parse_utc("2026-11-09T15:18:19Z"), Some(1_794_237_499));
        assert_eq!(parse_utc("2026-11-09 15:18:19Z"), None);
        assert_eq!(parse_utc("2026-13-09T15:18:19Z"), None);
        assert_eq!(parse_utc(""), None);
    }

    #[test]
    fn durations_read_like_people_talk() {
        assert_eq!(dur(63_930.0), "17 時 45 分");
        assert_eq!(dur(2442.7), "40 分 43 秒");
        assert_eq!(dur(11.8), "12 秒");
        assert_eq!(dur(0.2), "1 秒");
    }

    #[test]
    fn names_come_from_the_tree_that_ran_in_that_window() {
        let dir = std::env::temp_dir().join(format!("aib-plan-names-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        // 一批 11-06 07:53:51 結束、花 600 秒 ⇒ 07:41:51–07:54:51 內的列算它的
        let mut s = parse(
            r#"{"saved_s": 100, "with_s": 600, "repo": "ab12", "utc": "2026-11-06T07:53:51Z"}
{"saved_s": 5, "with_s": 10, "repo": "ab12", "utc": "2026-11-01T00:00:00Z"}
{"saved_s": 5, "with_s": 10, "repo": "../x", "utc": "2026-11-02T00:00:00Z"}"#,
        );
        let row = |t: &str, p: &str| {
            format!("{{\"test\": \"a\", \"inputs\": [\"x\"], \"time_utc\": \"{t}\", \"repo\": \"{p}\", \"repo_id\": \"ab12\"}}\n")
        };
        let pool = [
            row("2026-11-06T07:45:00Z", "/u/wt.noindex/cooldown-34d15"),
            row("2026-11-06T07:50:00Z", "/u/wt.noindex/cooldown-34d15"),
            row("2026-11-06T07:52:00Z", "/u/wt.noindex/other-tree"),
            row("2026-11-06T07:30:00Z", "/u/wt.noindex/too-early"),
            "{\"void\": {}}\n".to_string(),
        ]
        .concat();
        std::fs::write(dir.join("verify-ab12.rows.jsonl"), pool).unwrap();
        let cache = dir.join("cache").join("names.json");
        name_runs_cached(&dir, &cache, "k1", &mut s);
        assert_eq!(
            s.saves[0].name.as_deref(),
            Some("cooldown-34d15"),
            "最多列那棵"
        );
        assert_eq!(s.saves[0].seat(), Some("34d15"));
        assert_eq!(s.saves[1].name, None, "範圍內沒有列 ⇒ 查不到");
        assert_eq!(s.saves[2].name, None, "怪倉庫碼不開檔");
        // 快取:沿用池拿掉也照樣有名稱;key 換了才重掃
        std::fs::remove_file(dir.join("verify-ab12.rows.jsonl")).unwrap();
        let mut again = s.clone();
        again.saves.iter_mut().for_each(|e| e.name = None);
        name_runs_cached(&dir, &cache, "k1", &mut again);
        assert_eq!(again.saves[0].name.as_deref(), Some("cooldown-34d15"));
        name_runs_cached(&dir, &cache, "k2", &mut again);
        assert_eq!(again.saves[0].name, None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn seat_is_only_a_five_hex_suffix() {
        let s = |n: &str| Save {
            at: 0,
            saved_s: 1.0,
            ran: 0,
            reused: 0,
            with_s: 0.0,
            repo: String::new(),
            name: Some(n.into()),
        };
        assert_eq!(s("ez2aib-b117-rebase-ebb36").seat(), Some("ebb36"));
        assert_eq!(s("wt-570a9-attest").seat(), None);
        assert_eq!(s("x-ABCDE").seat(), None);
    }

    #[test]
    fn missing_file_is_none() {
        assert_eq!(
            read(Path::new("/nonexistent/aib-plan/test-stats.jsonl")),
            None
        );
    }
}
