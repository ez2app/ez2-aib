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
    /// 判斷(aib now --track/--why/--idea);只有目前那筆讀得到
    #[serde(skip)]
    pub review: Option<Review>,
    /// 歷史那一行回報的主軸符合度(aib now 回報判斷時才有;算校正次數用)
    /// (parse_log 自己讀:舊寫法 "on"/"off" 不能讓整行解析失敗)
    #[serde(skip)]
    pub track: Option<u8>,
}

/// 主軸符合度 ≥ 這個算在主軸上(aib now ON_TRACK_MIN 同一個門檻)
pub const ON_TRACK_MIN: u8 = 70;

/// aib now 的 --track:0~100 數字;舊寫法 "on" = 100、"off" = 0;其他不收
pub fn track_of(v: &serde_json::Value) -> Option<u8> {
    match v {
        serde_json::Value::Number(n) => n.as_u64().filter(|x| *x <= 100).map(|x| x as u8),
        serde_json::Value::String(s) if s == "on" => Some(100),
        serde_json::Value::String(s) if s == "off" => Some(0),
        _ => None,
    }
}

/// Andy 11-12 13:3x:每個階段完成都要回報 —— 有沒有偏離主軸、為什麼這麼做、主人沒想到的建議
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Review {
    /// 跟主軸的符合度 0~100(Andy 11-12 13:4x:用百分比)
    pub pct: u8,
    pub why: String,
    pub idea: String,
    pub at: i64,
}

impl Review {
    pub fn on_track(&self) -> bool {
        self.pct >= ON_TRACK_MIN
    }

    fn from_json(v: &serde_json::Value) -> Option<Review> {
        let s = |k: &str| v.get(k).and_then(|x| x.as_str()).map(str::to_string);
        Some(Review {
            pct: track_of(v.get("track")?)?,
            why: s("why")?,
            idea: s("idea")?,
            at: v.get("at").and_then(|x| x.as_i64()).unwrap_or(0),
        })
    }
}

/// 校正次數(Andy 11-12 13:4x):今天回報的符合度從「偏離」(< ON_TRACK_MIN)回到主軸上(≥)幾次
pub fn corrections(log: &[Entry], now: i64) -> u32 {
    let ds = day_start(now);
    let mut rows: Vec<&Entry> = log
        .iter()
        .filter(|e| e.seen >= ds && e.track.is_some())
        .collect();
    rows.sort_by_key(|e| e.seen);
    rows.windows(2)
        .filter(|w| w[0].track < Some(ON_TRACK_MIN) && w[1].track >= Some(ON_TRACK_MIN))
        .count() as u32
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Mainline {
    pub first: Option<Entry>,
    pub now: Option<Entry>,
    /// 今天的校正次數(見 corrections)
    pub corrections: u32,
    /// 今天寫了幾次「給主人的總結」(aib summary)、做了幾次交接(aib handoff 收據)
    pub summaries: u32,
    pub handoffs: u32,
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

/// 台北 MM-DD HH:MM(Andy 11-12 13:4x:時間都要含日期)
pub fn stamp(epoch: i64, _now: i64) -> String {
    let t = epoch + TAIPEI;
    let (h, mi) = (t.rem_euclid(86_400) / 3600, t.rem_euclid(3600) / 60);
    let (_, m, d) = civil(t.div_euclid(86_400));
    format!("{m:02}-{d:02} {h:02}:{mi:02}")
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
        review: v.get("review").and_then(Review::from_json),
        track: None,
    })
}

pub fn parse_log(text: &str) -> Vec<Entry> {
    text.lines()
        .filter_map(|l| {
            let v: serde_json::Value = serde_json::from_str(l).ok()?;
            let mut e = serde_json::from_value::<Entry>(v.clone()).ok()?;
            e.track = v.get("track").and_then(track_of);
            Some(e)
        })
        .collect()
}

/// tmux `show -wv @aib_seat` 的輸出 ⇒ 席碼(5 碼小寫十六進位);別的都不收
pub fn seat_from_tmux(out: &str) -> Option<String> {
    let s = out.trim();
    (s.len() == 5
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)))
    .then(|| s.to_string())
}

/// 這一筆要不要記：跟最後一筆的文字或開始時間不同才記
pub fn is_new(log: &[Entry], cur: &Entry) -> bool {
    log.last()
        .is_none_or(|l| l.item != cur.item || l.started_at != cur.started_at)
}

/// 今天最早的一筆(seen 在台北今天);今天還沒有 ⇒ 用目前那筆
pub fn first_today(log: &[Entry], cur: Option<&Entry>, now: i64) -> Option<Entry> {
    let ds = day_start(now);
    log.iter()
        .filter(|e| e.seen >= ds)
        .min_by_key(|e| e.seen)
        .cloned()
        .or_else(|| cur.cloned())
}

/// 今天(台北)的總結次數與交接次數。只讀，讀不到就是 0。
/// - 總結 = `<狀態根>/pending/to-maintainer-from-<席>-*.txt`(aib summary 每次寫一份),看檔案修改時間
/// - 總結也算 `aib now --track/--why/--idea` 的回報(Andy 11-12 14:3x 同意):歷史裡帶 track 的行，看 seen
/// - 交接 = `<狀態根>/state/handoff/<席>.receipts.jsonl` 每行一張收據，看 ts
pub fn day_counts(state_root: &Path, seat: &str, now: i64) -> (u32, u32) {
    let ds = day_start(now);
    let prefix = format!("to-maintainer-from-{seat}-");
    let summaries = state_root
        .parent()
        .map(|r| r.join("pending"))
        .and_then(|d| std::fs::read_dir(d).ok())
        .map(|it| {
            it.filter_map(Result::ok)
                .filter(|e| e.file_name().to_string_lossy().starts_with(&prefix))
                .filter_map(|e| e.metadata().ok()?.modified().ok())
                .filter_map(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .filter(|d| d.as_secs() as i64 >= ds)
                .count() as u32
        })
        .unwrap_or(0)
        + std::fs::read_to_string(
            state_root
                .join("seat-now")
                .join(format!("{seat}.history.jsonl")),
        )
        .unwrap_or_default()
        .lines()
        .filter_map(|l| serde_json::from_str::<serde_json::Value>(l).ok())
        .filter(|v| v.get("track").is_some_and(|t| !t.is_null()))
        .filter_map(|v| v.get("seen").and_then(|t| t.as_i64()))
        .filter(|t| *t >= ds)
        .count() as u32;
    let handoffs = std::fs::read_to_string(
        state_root
            .join("handoff")
            .join(format!("{seat}.receipts.jsonl")),
    )
    .unwrap_or_default()
    .lines()
    .filter_map(|l| serde_json::from_str::<serde_json::Value>(l).ok())
    .filter_map(|v| v.get("ts").and_then(|t| t.as_f64()))
    .filter(|t| *t as i64 >= ds)
    .count() as u32;
    (summaries, handoffs)
}

/// 別席：只讀(不替別席記任何東西)。aib now 歷史 + aib-plan 舊記(有就讀)取今天最早
pub fn peek(state_root: &Path, seat: &str, now: i64) -> Mainline {
    let cur = read_now(state_root, seat);
    let mut log = parse_log(
        &std::fs::read_to_string(
            state_root
                .join("seat-now")
                .join(format!("{seat}.history.jsonl")),
        )
        .unwrap_or_default(),
    );
    log.extend(parse_log(
        &std::fs::read_to_string(
            state_root
                .join("aib-plan")
                .join(format!("mainline-{seat}.jsonl")),
        )
        .unwrap_or_default(),
    ));
    let (summaries, handoffs) = day_counts(state_root, seat, now);
    Mainline {
        first: first_today(&log, cur.as_ref(), now),
        corrections: corrections(&log, now),
        summaries,
        handoffs,
        now: cur,
    }
}

fn bigrams(s: &str) -> std::collections::HashSet<(char, char)> {
    let cs: Vec<char> = s
        .chars()
        .filter(|c| c.is_alphanumeric())
        .flat_map(|c| c.to_lowercase())
        .collect();
    cs.windows(2).map(|w| (w[0], w[1])).collect()
}

/// 兩句主軸字面有多像(相鄰兩字的 Jaccard,0~1)
pub fn similarity(a: &str, b: &str) -> f64 {
    let (x, y) = (bigrams(a), bigrams(b));
    if x.is_empty() && y.is_empty() {
        return 1.0;
    }
    x.intersection(&y).count() as f64 / x.union(&y).count() as f64
}

/// 自稱「主軸內」,但目前主軸跟今天一開始的主軸字面幾乎不同 ⇒ 值得人看一眼(只是提示，不是判定)
pub const DRIFT_HINT_BELOW: f64 = 0.15;

pub fn drift_hint(m: &Mainline) -> bool {
    match (&m.first, &m.now) {
        (Some(f), Some(n)) => {
            n.review.as_ref().is_some_and(Review::on_track)
                && f.started_at != n.started_at
                && similarity(&f.item, &n.item) < DRIFT_HINT_BELOW
        }
        _ => false,
    }
}

/// 讀現況 + 補記 + 算兩行要的資料。寫記錄失敗不影響顯示。
pub fn refresh(state_root: &Path, seat: &str, now: i64) -> Mainline {
    let cur = read_now(state_root, seat).map(|mut e| {
        if e.seen == 0 {
            e.seen = now;
        }
        e
    });
    // aib now 自己寫的歷史(seat_now.append_history,11-12 起);有這份就不再自己記
    let hist_path = state_root
        .join("seat-now")
        .join(format!("{seat}.history.jsonl"));
    let hist = std::fs::read_to_string(&hist_path).ok();
    let dir = state_root.join("aib-plan");
    let path = dir.join(format!("mainline-{seat}.jsonl"));
    let mut log = parse_log(&std::fs::read_to_string(&path).unwrap_or_default());
    if let Some(c) = cur.as_ref().filter(|_| hist.is_none()) {
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
    // 舊 aib 時期自己記的 + aib now 的歷史合在一起看，取今天最早那筆
    log.extend(parse_log(hist.as_deref().unwrap_or_default()));
    let (summaries, handoffs) = day_counts(state_root, seat, now);
    Mainline {
        first: first_today(&log, cur.as_ref(), now),
        corrections: corrections(&log, now),
        summaries,
        handoffs,
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
            review: None,
            track: None,
        }
    }

    #[test]
    fn taipei_stamp_and_day_start() {
        let ds = day_start(NOW);
        assert_eq!(stamp(ds, NOW), "11-12 00:00");
        assert_eq!(stamp(ds + 9 * 60, NOW), "11-12 00:09");
        assert_eq!(stamp(ds - 60, NOW), "11-11 23:59");
        // 跨年
        assert_eq!(stamp(1_798_761_600 - TAIPEI, NOW), "01-01 00:00");
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

    #[test]
    fn seat_code_from_tmux_and_review_from_seat_now() {
        assert_eq!(seat_from_tmux("a63ab\n").as_deref(), Some("a63ab"));
        for bad in ["", "A63AB", "a63a", "a63abc", "../x1", "zzzzz"] {
            assert!(seat_from_tmux(bad).is_none(), "{bad}");
        }
        let dir = std::env::temp_dir().join(format!("aib-plan-rv-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("seat-now")).unwrap();
        let j = |track: &str| {
            format!("{{\"item\":\"做 A\",\"started_at\":1,\"at\":2,\"review\":{{\"track\":{track},\"why\":\"因為\",\"idea\":\"無\",\"at\":2}}}}")
        };
        std::fs::write(dir.join("seat-now/s3.json"), j("65")).unwrap();
        let r = read_now(&dir, "s3").unwrap().review.unwrap();
        assert_eq!(r.pct, 65);
        assert!(!r.on_track(), "70 以下算偏離");
        assert_eq!((r.why.as_str(), r.idea.as_str()), ("因為", "無"));
        std::fs::write(dir.join("seat-now/s3.json"), j("\"on\"")).unwrap();
        assert_eq!(
            read_now(&dir, "s3").unwrap().review.unwrap().pct,
            100,
            "舊寫法 on = 100"
        );
        for bad in ["101", "-1", "\"maybe\"", "null"] {
            std::fs::write(dir.join("seat-now/s3.json"), j(bad)).unwrap();
            assert!(read_now(&dir, "s3").unwrap().review.is_none(), "{bad}");
        }
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn drift_hint_flags_on_track_claims_with_a_different_topic() {
        let rv = |track: &str| {
            Some(Review {
                pct: if track == "on" { 90 } else { 30 },
                why: "w".into(),
                idea: "i".into(),
                at: 0,
            })
        };
        let m = |first: &str, now: &str, track: &str| Mainline {
            corrections: 0,
            summaries: 0,
            handoffs: 0,
            first: Some(e(first, 1, 1)),
            now: Some(Entry {
                review: rv(track),
                ..e(now, 2, 2)
            }),
        };
        assert!(similarity("aib-plan 主軸兩行", "aib-plan 主軸兩行已裝") > 0.6);
        assert!(similarity("席位接續 v2 決定", "主機發熱三條修法") < 0.15);
        assert!(drift_hint(&m("席位接續 v2 決定", "主機發熱三條修法", "on")));
        assert!(
            !drift_hint(&m("席位接續 v2 決定", "主機發熱三條修法", "off")),
            "自己說偏離就不用提示"
        );
        assert!(!drift_hint(&m(
            "aib-plan 主軸兩行",
            "aib-plan 主軸兩行已裝",
            "on"
        )));
        let same_start = Mainline {
            corrections: 0,
            summaries: 0,
            handoffs: 0,
            first: Some(e("A 主題", 2, 2)),
            now: Some(Entry {
                review: rv("on"),
                ..e("完全不同", 2, 3)
            }),
        };
        assert!(!drift_hint(&same_start), "同一個主軸(開始時間相同)只是改字");
    }

    #[test]
    fn corrections_count_returns_to_the_mainline_today() {
        let ds = day_start(NOW);
        let t = |seen: i64, track: Option<u8>| Entry {
            track,
            ..e("x", 1, seen)
        };
        let log = parse_log(&format!(
            "{{\"item\":\"x\",\"started_at\":1,\"seen\":{},\"track\":40}}\n{{\"item\":\"x\",\"started_at\":1,\"seen\":{},\"track\":\"on\"}}\n",
            ds + 10,
            ds + 20
        ));
        assert_eq!(log[0].track, Some(40));
        assert_eq!(log[1].track, Some(100));
        assert_eq!(corrections(&log, NOW), 1);
        let log = vec![
            t(ds - 100, Some(10)), // 昨天：不算
            t(ds + 1, Some(90)),
            t(ds + 2, None), // 只換主軸、沒回報：跳過
            t(ds + 3, Some(50)),
            t(ds + 4, Some(80)), // 1
            t(ds + 5, Some(60)),
            t(ds + 6, Some(69)),
            t(ds + 7, Some(70)), // 2
            t(ds + 8, Some(100)),
        ];
        assert_eq!(corrections(&log, NOW), 2);
        assert_eq!(corrections(&[], NOW), 0);
    }

    #[test]
    fn day_counts_summaries_and_handoffs_today() {
        let root = std::env::temp_dir().join(format!("aib-plan-dc-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        let state = root.join("state");
        std::fs::create_dir_all(state.join("handoff")).unwrap();
        std::fs::create_dir_all(state.join("seat-now")).unwrap();
        std::fs::create_dir_all(root.join("pending")).unwrap();
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs() as i64;
        let ds = day_start(now);
        std::fs::write(
            state.join("handoff/s5.receipts.jsonl"),
            format!(
                "{{\"ts\": {}}}\n{{\"ts\": {}.5}}\nbad\n{{\"ts\": {}}}\n",
                ds - 10,
                ds + 1,
                now
            ),
        )
        .unwrap();
        for f in [
            "to-maintainer-from-s5-1112-1.txt",
            "to-maintainer-from-s5-1112-2.txt",
            "to-maintainer-from-s55-x.txt",
            "SUMMARY-1d-s5.md",
        ] {
            std::fs::write(root.join("pending").join(f), "x").unwrap();
        }
        // 剛寫的檔 = 今天;交接 3 張裡 1 張是昨天、1 行壞的
        assert_eq!(day_counts(&state, "s5", now), (2, 2));
        // aib now 帶判斷的回報也算總結：今天 2 行有 track(1 行 null 不算)、昨天 1 行、沒 track 1 行
        std::fs::write(
            state.join("seat-now/s5.history.jsonl"),
            format!(
                "{{\"item\":\"a\",\"started_at\":1,\"seen\":{},\"track\":90}}\n\
                 {{\"item\":\"a\",\"started_at\":1,\"seen\":{},\"track\":80}}\n\
                 {{\"item\":\"a\",\"started_at\":1,\"seen\":{},\"track\":null}}\n\
                 {{\"item\":\"a\",\"started_at\":1,\"seen\":{}}}\n\
                 {{\"item\":\"a\",\"started_at\":1,\"seen\":{},\"track\":70}}\n",
                ds + 5,
                now,
                now,
                now,
                ds - 5
            ),
        )
        .unwrap();
        assert_eq!(day_counts(&state, "s5", now), (4, 2));
        assert_eq!(day_counts(&state, "zz", now), (0, 0));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn peek_never_writes() {
        let dir = std::env::temp_dir().join(format!("aib-plan-pk-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("seat-now")).unwrap();
        std::fs::write(
            dir.join("seat-now/s4.json"),
            "{\"item\":\"做 X\",\"started_at\":1,\"at\":2}",
        )
        .unwrap();
        let m = peek(&dir, "s4", NOW);
        assert_eq!(m.now.unwrap().item, "做 X");
        assert!(!dir.join("aib-plan").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// aib now 有寫歷史 ⇒ 讀它、不再自己記;跟自己舊記的合併取今天最早
    #[test]
    fn prefers_aib_now_history_and_merges_old_log() {
        let dir = std::env::temp_dir().join(format!("aib-plan-mh-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(dir.join("seat-now")).unwrap();
        std::fs::create_dir_all(dir.join("aib-plan")).unwrap();
        let ds = day_start(NOW);
        std::fs::write(
            dir.join("aib-plan/mainline-s2.jsonl"),
            format!(
                "{{\"item\":\"舊記的\",\"started_at\":{0},\"seen\":{0}}}\n",
                ds + 540
            ),
        )
        .unwrap();
        std::fs::write(
            dir.join("seat-now/s2.history.jsonl"),
            format!(
                "{{\"item\":\"昨天\",\"started_at\":{0},\"seen\":{0}}}\n{{\"item\":\"後來\",\"started_at\":{1},\"seen\":{1}}}\n",
                ds - 60,
                ds + 3600
            ),
        )
        .unwrap();
        std::fs::write(
            dir.join("seat-now/s2.json"),
            format!(
                "{{\"item\":\"後來\",\"started_at\":{0},\"at\":{0}}}",
                ds + 3600
            ),
        )
        .unwrap();
        let m = refresh(&dir, "s2", NOW);
        assert_eq!(m.first.unwrap().item, "舊記的");
        assert_eq!(m.now.unwrap().item, "後來");
        let own = std::fs::read_to_string(dir.join("aib-plan/mainline-s2.jsonl")).unwrap();
        assert_eq!(own.lines().count(), 1, "有 aib now 歷史就不再自己記");
        let _ = std::fs::remove_dir_all(&dir);
    }
}
