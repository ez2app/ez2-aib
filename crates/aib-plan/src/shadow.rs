//! 影子續跑省下的時間(Andy 11-12 23:5x「在目前目標下方再新增一個，影子續跑：共省多少時間，
//! 只要有省成功的就在下方的影子續跑項目中記錄」)。
//!
//! 資料來源 = `aib test` 每跑一批寫一列的 `verify-shadow/test-stats.jsonl`
//! (`saved_s` = 不沿用時估計要花的秒數 − 這次實際花的秒數)。
//! 只收 `saved_s` > 0 的列(省成功);0、負數、沒有數字、時間壞掉的列都不算。

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
            })
        })
        .collect();
    saves.sort_by(|a, b| b.at.cmp(&a.at));
    Savings {
        total_s: saves.iter().map(|s| s.saved_s).sum(),
        saves,
    }
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
    fn missing_file_is_none() {
        assert_eq!(
            read(Path::new("/nonexistent/aib-plan/test-stats.jsonl")),
            None
        );
    }
}
