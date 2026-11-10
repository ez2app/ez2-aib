//! B12: a temporary rule must say when it ends, on what condition, and who answers for it.
//!
//! A heading in the seats' RULES file that carries 【暫時】 must, on the same line, carry
//! `【到期:YYYY-MM-DD HH:MM 台北 / 解除條件:<text> / 負責席:<seat>】` (RULES §15.8 is the
//! model). A heading that also carries 【失效】 or 【已解除】 has been dealt with and is skipped.
//! Only headings count: a paragraph that quotes 【暫時】 is not an entry.
//!
//! Taipei is a fixed +08:00 (no daylight saving), so the due time is turned into epoch seconds
//! by arithmetic, the same on every machine whatever its time zone.
//!
//! Not a security boundary: the file is writable by the same account. This catches a
//! temporary rule nobody remembered to lift.

pub const TEMPORARY: &str = "【暫時】";
pub const RETIRED: [&str; 2] = ["【失效】", "【已解除】"];
const TAIPEI_OFFSET_S: i64 = 8 * 3600;

/// What one temporary heading says.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// Due later (or exactly now): nothing to do.
    Open { due: i64, owner: String },
    /// Past its due time and not marked retired.
    Expired {
        due: i64,
        owner: String,
        condition: String,
    },
    /// Marked 【失效】 / 【已解除】.
    Retired,
    /// A field is missing or unreadable: the rule cannot be checked.
    Missing(&'static str),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    /// 1-based line number in the file.
    pub line: usize,
    /// The heading without its leading `#`s.
    pub title: String,
    /// The due time as written (`2026-11-11 03:00`), when it could be read.
    pub due_text: Option<String>,
    pub verdict: Verdict,
}

/// Exit code for a scan: 2 when any entry is missing a field, else 1 when any expired, else 0.
pub fn exit_code(entries: &[Entry]) -> u8 {
    if entries
        .iter()
        .any(|e| matches!(e.verdict, Verdict::Missing(_)))
    {
        2
    } else if entries
        .iter()
        .any(|e| matches!(e.verdict, Verdict::Expired { .. }))
    {
        1
    } else {
        0
    }
}

/// Days from 1970-01-01 to the civil date (proleptic Gregorian).
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = (if y >= 0 { y } else { y - 399 }) / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

fn days_in_month(y: i64, m: i64) -> i64 {
    match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ if (y % 4 == 0 && y % 100 != 0) || y % 400 == 0 => 29,
        _ => 28,
    }
}

fn num(s: &str, len: usize) -> Option<i64> {
    if s.len() == len && s.bytes().all(|b| b.is_ascii_digit()) {
        s.parse().ok()
    } else {
        None
    }
}

/// `YYYY-MM-DD HH:MM` in Taipei time ⇒ epoch seconds.
pub fn taipei_epoch(text: &str) -> Option<i64> {
    let (date, time) = text.split_once(' ')?;
    let mut d = date.split('-');
    let (y, m, day) = (num(d.next()?, 4)?, num(d.next()?, 2)?, num(d.next()?, 2)?);
    let (h, min) = time.split_once(':')?;
    let (h, min) = (num(h, 2)?, num(min, 2)?);
    if d.next().is_some()
        || !(1..=12).contains(&m)
        || day < 1
        || day > days_in_month(y, m)
        || h > 23
        || min > 59
    {
        return None;
    }
    Some(days_from_civil(y, m, day) * 86_400 + h * 3600 + min * 60 - TAIPEI_OFFSET_S)
}

fn seat_ok(s: &str) -> bool {
    (4..=8).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_digit() || b.is_ascii_lowercase())
}

fn field<'a>(part: &'a str, name: &str) -> Option<&'a str> {
    let rest = part.trim().strip_prefix(name)?;
    let rest = rest.strip_prefix(':').or_else(|| rest.strip_prefix(':'))?;
    Some(rest.trim()).filter(|v| !v.is_empty())
}

fn judge(heading: &str, now: i64) -> (Option<String>, Verdict) {
    if RETIRED.iter().any(|r| heading.contains(r)) {
        return (None, Verdict::Retired);
    }
    let Some(start) = heading.find("【到期") else {
        return (None, Verdict::Missing("no_due"));
    };
    let after = &heading[start + "【到期".len()..];
    let Some(end) = after.find('】') else {
        return (None, Verdict::Missing("no_due"));
    };
    let inner = after[..end]
        .strip_prefix(':')
        .or_else(|| after[..end].strip_prefix(':'))
        .unwrap_or("");
    let parts: Vec<&str> = inner.split(" / ").collect();
    if parts.len() < 3 {
        return (None, Verdict::Missing("fields"));
    }
    let due_text = parts[0]
        .trim()
        .strip_suffix("台北")
        .map(str::trim)
        .unwrap_or("");
    let Some(due) = taipei_epoch(due_text) else {
        return (None, Verdict::Missing("bad_due"));
    };
    let due_text = Some(due_text.to_string());
    let condition = parts[1..parts.len() - 1].join(" / ");
    let Some(condition) = field(&condition, "解除條件") else {
        return (due_text, Verdict::Missing("no_condition"));
    };
    let Some(owner) = field(parts[parts.len() - 1], "負責席").filter(|s| seat_ok(s)) else {
        return (due_text, Verdict::Missing("no_owner"));
    };
    let owner = owner.to_string();
    if now > due {
        (
            due_text,
            Verdict::Expired {
                due,
                owner,
                condition: condition.to_string(),
            },
        )
    } else {
        (due_text, Verdict::Open { due, owner })
    }
}

/// Every heading of `text` that carries 【暫時】, judged at `now` (epoch seconds).
pub fn scan(text: &str, now: i64) -> Vec<Entry> {
    text.lines()
        .enumerate()
        .filter_map(|(i, raw)| {
            let l = raw.trim_start();
            if !l.starts_with('#') || !l.contains(TEMPORARY) {
                return None;
            }
            let title = l.trim_start_matches('#').trim().to_string();
            let (due_text, verdict) = judge(l, now);
            Some(Entry {
                line: i + 1,
                title,
                due_text,
                verdict,
            })
        })
        .collect()
}

fn short(title: &str) -> String {
    let t: String = title.chars().take(60).collect();
    if t.len() < title.len() {
        format!("{t}…")
    } else {
        t
    }
}

/// One line per entry, for people.
pub fn line(e: &Entry) -> String {
    let due = e.due_text.as_deref().unwrap_or("?");
    match &e.verdict {
        Verdict::Open { owner, .. } => format!(
            "OPEN    L{} 到期 {due} 台北 負責席 {owner} :: {}",
            e.line,
            short(&e.title)
        ),
        Verdict::Expired {
            owner, condition, ..
        } => format!(
            "EXPIRED L{} 到期 {due} 台北 負責席 {owner} 解除條件 {condition} :: {}",
            e.line,
            short(&e.title)
        ),
        Verdict::Retired => format!("RETIRED L{} :: {}", e.line, short(&e.title)),
        Verdict::Missing(why) => format!(
            "MISSING L{} {why}: 要寫【到期:YYYY-MM-DD HH:MM 台北 / 解除條件:… / 負責席:…】 :: {}",
            e.line,
            short(&e.title)
        ),
    }
}

/// Who gets told what: each expired entry goes to its owner and to `captain` (once when they
/// are the same seat); entries missing a field go to `captain`.
pub fn messages(entries: &[Entry], captain: &str) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for e in entries {
        let text = format!("RULES 暫時條目:{}", line(e));
        match &e.verdict {
            Verdict::Expired { owner, .. } => {
                out.push((owner.clone(), text.clone()));
                if owner != captain {
                    out.push((captain.to_string(), text));
                }
            }
            Verdict::Missing(_) => out.push((captain.to_string(), text)),
            Verdict::Open { .. } | Verdict::Retired => {}
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const R158: &str = "### 【暫時】§15.8 暫時規則:A/A 量測期間 CI 先行(Andy 11-10 20:2x「依你的建議開始」;到 A/A 第 2 輪結束 + 043dd 宣告解除為止)【到期:2026-11-11 03:00 台北 / 解除條件:043dd「§15.8 解除」信 / 負責席:a63ab】";

    fn at(t: &str) -> i64 {
        taipei_epoch(t).expect("time")
    }

    #[test]
    fn taipei_is_utc_plus_eight_everywhere() {
        // 2026-11-11 03:00 Taipei = 2026-11-10 19:00 UTC
        assert_eq!(at("2026-11-11 03:00"), 1_794_337_200);
        assert_eq!(at("1970-01-01 08:00"), 0);
        assert_eq!(at("2028-02-29 00:00") + 86_400, at("2028-03-01 00:00"));
        for bad in [
            "2026-13-01 00:00",
            "2026-02-29 00:00",
            "2026-11-11 24:00",
            "2026-11-11 3:00",
            "2026/11/11 03:00",
            "2026-11-11",
        ] {
            assert_eq!(taipei_epoch(bad), None, "{bad}");
        }
    }

    #[test]
    fn the_rules_file_entry_reads_whole() {
        let es = scan(R158, at("2026-11-11 02:00"));
        let e = &es[0];
        assert_eq!(e.line, 1);
        assert_eq!(e.due_text.as_deref(), Some("2026-11-11 03:00"));
        assert_eq!(
            e.verdict,
            Verdict::Open {
                due: at("2026-11-11 03:00"),
                owner: "a63ab".into()
            }
        );
    }

    #[test]
    fn an_expired_entry_is_reported() {
        let due = at("2026-11-11 03:00");
        // the due minute itself is not past
        assert!(matches!(scan(R158, due)[0].verdict, Verdict::Open { .. }));
        let es = scan(R158, due + 1);
        assert_eq!(
            es[0].verdict,
            Verdict::Expired {
                due,
                owner: "a63ab".into(),
                condition: "043dd「§15.8 解除」信".into()
            }
        );
        assert_eq!(exit_code(&es), 1);
        assert!(line(&es[0]).starts_with("EXPIRED L1 到期 2026-11-11 03:00 台北 負責席 a63ab"));
    }

    #[test]
    fn a_missing_field_is_an_error() {
        let now = at("2026-11-10 12:00");
        for (h, why) in [
            ("## 【暫時】§9.9 沒有到期", "no_due"),
            (
                "## 【暫時】§9.9【到期:2026-11-11 03:00 台北 / 負責席:a63ab】",
                "fields",
            ),
            (
                "## 【暫時】§9.9【到期:2026-11-11 03:00 / 解除條件:x / 負責席:a63ab】",
                "bad_due",
            ),
            (
                "## 【暫時】§9.9【到期:2026-13-11 03:00 台北 / 解除條件:x / 負責席:a63ab】",
                "bad_due",
            ),
            (
                "## 【暫時】§9.9【到期:2026-11-11 03:00 台北 / 解除條件: / 負責席:a63ab】",
                "no_condition",
            ),
            (
                "## 【暫時】§9.9【到期:2026-11-11 03:00 台北 / 條件:x / 負責席:a63ab】",
                "no_condition",
            ),
            (
                "## 【暫時】§9.9【到期:2026-11-11 03:00 台北 / 解除條件:x / 負責席:A63AB】",
                "no_owner",
            ),
            (
                "## 【暫時】§9.9【到期:2026-11-11 03:00 台北 / 解除條件:x / 負責人:a63ab】",
                "no_owner",
            ),
        ] {
            let es = scan(h, now);
            assert_eq!(es[0].verdict, Verdict::Missing(why), "{h}");
            assert_eq!(exit_code(&es), 2, "{h}");
        }
        // a missing field outranks an expired one
        let both = format!("{R158}\n## 【暫時】沒有到期");
        assert_eq!(exit_code(&scan(&both, at("2027-01-01 00:00"))), 2);
    }

    #[test]
    fn a_retired_entry_is_skipped() {
        for mark in RETIRED {
            let h = R158.replace("【暫時】", &format!("【暫時】{mark}"));
            let es = scan(&h, at("2027-01-01 00:00"));
            assert_eq!(es[0].verdict, Verdict::Retired);
            assert_eq!(exit_code(&es), 0);
        }
    }

    #[test]
    fn only_headings_count_and_each_is_judged() {
        let text = format!(
            "# RULES\n\n內文提到【暫時】不算\n- 【暫時】清單也不算\n{R158}\n\n## §16 一般規則\n  ## 【暫時】§17 縮排標題【到期:2026-11-20 00:00 台北 / 解除條件:x / 負責席:1ced7】\n"
        );
        let es = scan(&text, at("2026-11-12 00:00"));
        assert_eq!(es.iter().map(|e| e.line).collect::<Vec<_>>(), [5, 8]);
        assert!(matches!(es[0].verdict, Verdict::Expired { .. }));
        assert!(matches!(es[1].verdict, Verdict::Open { .. }));
        assert_eq!(exit_code(&es), 1);
        assert_eq!(exit_code(&scan("# nothing temporary\n", 0)), 0);
    }

    #[test]
    fn full_width_colons_and_a_slash_in_the_condition_are_read() {
        let h =
            "## 【暫時】§9【到期:2026-11-11 03:00 台北 / 解除條件:A / B 都做完 / 負責席:043dd】";
        let es = scan(h, at("2026-11-12 00:00"));
        assert_eq!(
            es[0].verdict,
            Verdict::Expired {
                due: at("2026-11-11 03:00"),
                owner: "043dd".into(),
                condition: "A / B 都做完".into()
            }
        );
    }

    #[test]
    fn the_owner_and_the_captain_are_told_once_each() {
        let now = at("2027-01-01 00:00");
        let own = scan(R158, now);
        assert_eq!(
            messages(&own, "a63ab")
                .iter()
                .map(|m| m.0.as_str())
                .collect::<Vec<_>>(),
            ["a63ab"]
        );
        let other = scan(&R158.replace("負責席:a63ab", "負責席:043dd"), now);
        assert_eq!(
            messages(&other, "a63ab")
                .iter()
                .map(|m| m.0.as_str())
                .collect::<Vec<_>>(),
            ["043dd", "a63ab"]
        );
        let missing = scan("## 【暫時】x", now);
        assert_eq!(
            messages(&missing, "a63ab")
                .iter()
                .map(|m| m.0.as_str())
                .collect::<Vec<_>>(),
            ["a63ab"]
        );
        assert!(messages(&scan(R158, at("2026-11-10 00:00")), "a63ab").is_empty());
    }
}
