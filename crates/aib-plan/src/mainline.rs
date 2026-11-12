//! 最上面兩行：本席一開始主軸 / 目前主軸(Andy 11-12 13:1x)。
//!
//! - 目前主軸 = `<狀態根>/state/seat-now/<席>.json`(`aib now` 寫的)的 item + started_at。
//! - 一開始主軸 = 本席「台北今天」第一個主項目。`aib now` 沒留歷史，所以 aib-plan 自己記:
//!   每看到主項目換了(文字或開始時間不同)就在 `<狀態根>/state/aib-plan/mainline-<席>.jsonl`
//!   加一行 `{"item","started_at","seen"}`;今天第一行(看 seen)就是「一開始」。
//! - 時間一律台北(UTC+8,沒有日光節約);本機時區可能不是台北(席位機是 AST)。

use serde::{Deserialize, Serialize};
use std::path::Path;

pub const TAIPEI: i64 = 8 * 3600;

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Entry {
    pub item: String,
    pub started_at: i64,
    /// 這一筆最早被看到的時間(秒)
    pub seen: i64,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Mainline {
    pub first: Option<Entry>,
    pub now: Option<Entry>,
}

/// 台北今天 00:00 的 epoch 秒
pub fn day_start(now: i64) -> i64 {
    (now + TAIPEI).div_euclid(86_400) * 86_400 - TAIPEI
}

fn civil(days: i64) -> (i64, u32, u32) {
    // Howard Hinnant days_from_civil 的反函式
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    (yoe + era * 400 + i64::from(m <= 2), m, d)
}

/// 台北 HH:MM;不是今天就加 MM-DD
pub fn stamp(epoch: i64, now: i64) -> String {
    let t = epoch + TAIPEI;
    let (h, mi) = (t.rem_euclid(86_400) / 3600, t.rem_euclid(3600) / 60);
    if epoch >= day_start(now) && epoch < day_start(now) + 86_400 {
        format!("{h:02}:{mi:02}")
    } else {
        let (_, m, d) = civil(t.div_euclid(86_400));
        format!("{m:02}-{d:02} {h:02}:{mi:02}")
    }
}

/// 讀 seat-now/<席>.json;讀不到 / 壞掉 / --clear(idle)⇒ None
pub fn read_now(state_root: &Path, seat: &str) -> Option<Entry> {
    let t =
        std::fs::read_to_string(state_root.join("seat-now").join(format!("{seat}.json"))).ok()?;
    let v: serde_json::Value = serde_json::from_str(&t).ok()?;
    if v.get("idle").and_then(|x| x.as_bool()) == Some(true) {
        return None;
    }
    let item = v.get("item")?.as_str()?.trim().to_string();
    if item.is_empty() {
        return None;
    }
    let at = v.get("at").and_then(|x| x.as_i64()).unwrap_or(0);
    Some(Entry {
        item,
        started_at: v.get("started_at").and_then(|x| x.as_i64()).unwrap_or(at),
        seen: at,
    })
}

pub fn parse_log(text: &str) -> Vec<Entry> {
    text.lines()
        .filter_map(|l| serde_json::from_str::<Entry>(l).ok())
        .collect()
}

/// 這一筆要不要記：跟最後一筆的文字或開始時間不同才記
pub fn is_new(log: &[Entry], cur: &Entry) -> bool {
    log.last()
        .is_none_or(|l| l.item != cur.item || l.started_at != cur.started_at)
}

/// 今天第一筆(seen 在台北今天);今天還沒有 ⇒ 用目前那筆
pub fn first_today(log: &[Entry], cur: Option<&Entry>, now: i64) -> Option<Entry> {
    let ds = day_start(now);
    log.iter()
        .find(|e| e.seen >= ds)
        .cloned()
        .or_else(|| cur.cloned())
}

/// 讀現況 + 補記 + 算兩行要的資料。寫記錄失敗不影響顯示。
pub fn refresh(state_root: &Path, seat: &str, now: i64) -> Mainline {
    let cur = read_now(state_root, seat).map(|mut e| {
        if e.seen == 0 {
            e.seen = now;
        }
        e
    });
    let dir = state_root.join("aib-plan");
    let path = dir.join(format!("mainline-{seat}.jsonl"));
    let mut log = parse_log(&std::fs::read_to_string(&path).unwrap_or_default());
    if let Some(c) = &cur {
        if is_new(&log, c) {
            if let Ok(line) = serde_json::to_string(c) {
                use std::io::Write;
                let _ = std::fs::create_dir_all(&dir);
                if let Ok(mut f) = std::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&path)
                {
                    let _ = writeln!(f, "{line}");
                }
            }
            log.push(c.clone());
        }
    }
    Mainline {
        first: first_today(&log, cur.as_ref(), now),
        now: cur,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // 2026-11-12 13:20 台北 = 05:20Z
    const NOW: i64 = 1_794_460_800;

    fn e(item: &str, started_at: i64, seen: i64) -> Entry {
        Entry {
            item: item.into(),
            started_at,
            seen,
        }
    }

    #[test]
    fn taipei_stamp_and_day_start() {
        let ds = day_start(NOW);
        assert_eq!(stamp(ds, NOW), "00:00");
        assert_eq!(stamp(ds + 9 * 60, NOW), "00:09");
        // 前一天 → 帶日期
        assert_eq!(stamp(ds - 60, NOW), "11-11 23:59");
        assert_eq!(day_start(ds), ds);
        assert_eq!(day_start(ds - 1), ds - 86_400);
    }

    #[test]
    fn first_today_picks_earliest_seen_today() {
        let ds = day_start(NOW);
        let log = vec![
            e("昨天的", ds - 7200, ds - 7200),
            e("今天第一", ds - 7200, ds + 540),
            e("後來", ds + 3600, ds + 3600),
        ];
        let cur = e("後來", ds + 3600, NOW);
        assert_eq!(first_today(&log, Some(&cur), NOW).unwrap().item, "今天第一");
        assert_eq!(
            first_today(&log[..1], Some(&cur), NOW).unwrap().item,
            "後來"
        );
        assert!(first_today(&[], None, NOW).is_none());
    }

    #[test]
    fn only_changes_are_logged() {
        let log = vec![e("a", 1, 1)];
        assert!(!is_new(&log, &e("a", 1, 99)));
        assert!(is_new(&log, &e("a", 2, 99)));
        assert!(is_new(&log, &e("b", 1, 99)));
        assert!(is_new(&[], &e("a", 1, 1)));
        assert_eq!(
            parse_log("{\"item\":\"a\",\"started_at\":1,\"seen\":2}\nbad\n").len(),
            1
        );
    }

    #[test]
    fn refresh_reads_seat_now_and_appends_once() {
        let dir = std::env::temp_dir().join(format!("aib-plan-ml-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("seat-now")).unwrap();
        let ds = day_start(NOW);
        std::fs::write(
            dir.join("seat-now/s1.json"),
            format!(
                "{{\"item\":\"做 X\",\"started_at\":{},\"at\":{}}}",
                ds + 60,
                ds + 120
            ),
        )
        .unwrap();
        let m = refresh(&dir, "s1", NOW);
        assert_eq!(m.now.as_ref().unwrap().item, "做 X");
        assert_eq!(m.first.as_ref().unwrap().item, "做 X");
        let _ = refresh(&dir, "s1", NOW);
        let log = std::fs::read_to_string(dir.join("aib-plan/mainline-s1.jsonl")).unwrap();
        assert_eq!(log.lines().count(), 1);
        std::fs::write(
            dir.join("seat-now/s1.json"),
            format!(
                "{{\"item\":\"做 Y\",\"started_at\":{},\"at\":{}}}",
                ds + 600,
                ds + 600
            ),
        )
        .unwrap();
        let m = refresh(&dir, "s1", NOW);
        assert_eq!(m.first.unwrap().item, "做 X");
        assert_eq!(m.now.unwrap().item, "做 Y");
        // --clear ⇒ 目前沒有，一開始照留
        std::fs::write(
            dir.join("seat-now/s1.json"),
            "{\"idle\":true,\"item\":\"\"}",
        )
        .unwrap();
        let m = refresh(&dir, "s1", NOW);
        assert!(m.now.is_none());
        assert_eq!(m.first.unwrap().item, "做 X");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
