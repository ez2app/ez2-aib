//! S107-product slice 3b (DECISION-council-andyopen-519d0-1117 a1, Andy 11-17 20:4x 全部同意):
//! how much the 「AI 推進」 button may still spend, counted from drive's own drive_send events
//! (slice 3), never a second ledger. Defaults: 20 a day for the whole fleet, 1 an hour for one
//! card, 30 minutes for one run; tokens are added up when an event carries them, never a limit.
//!
//! `aib-drive budget [--card ID] [--json]` answers whether one more push may go now and, when
//! not, why (`day_full` / `card_hour_full`). It only reads; the button (S107-write) asks it
//! before it sends and writes a `held` event itself when the answer is no.
//!
//! Settings: `<state root>/drive/budget.json`, every key optional —
//! `{"per_day": 20, "per_card_hour": 1, "run_timeout_min": 30, "tz_offset_min": 480}`. The day
//! is the day in that offset (480 = Taipei). A file that cannot be read or parsed is said on
//! stderr and the defaults are used; a value of 0 or less for a limit is refused the same way.

use crate::send_events::{PROG, SEND};
use aib_ledger::events;
use serde_json::{json, Value};
use std::path::Path;

/// The button's action word in drive_send events: only these count toward the budget (act's
/// own nudges and notices do not).
pub const PUSH_ACTION: &str = "push";
/// The settings file under drive's folder.
pub const FILE: &str = "budget.json";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    pub per_day: u32,
    pub per_card_hour: u32,
    pub run_timeout_min: u32,
    pub tz_offset_min: i32,
}

impl Default for Limits {
    fn default() -> Limits {
        Limits {
            per_day: 20,
            per_card_hour: 1,
            run_timeout_min: 30,
            tz_offset_min: 480,
        }
    }
}

/// The limits from the settings text; `Err` says why it was not used (the caller keeps the
/// defaults). A missing key keeps its default.
pub fn parse_limits(text: &str) -> Result<Limits, String> {
    let v: Value = serde_json::from_str(text).map_err(|e| format!("{FILE}: {e}"))?;
    let mut l = Limits::default();
    let pos = |k: &str, d: u32| -> Result<u32, String> {
        match v.get(k) {
            None => Ok(d),
            Some(x) => x
                .as_u64()
                .filter(|n| (1..=10_000).contains(n))
                .and_then(|n| u32::try_from(n).ok())
                .ok_or_else(|| format!("{FILE}: {k} must be a whole number 1-10000")),
        }
    };
    l.per_day = pos("per_day", l.per_day)?;
    l.per_card_hour = pos("per_card_hour", l.per_card_hour)?;
    l.run_timeout_min = pos("run_timeout_min", l.run_timeout_min)?;
    if let Some(x) = v.get("tz_offset_min") {
        l.tz_offset_min = x
            .as_i64()
            .filter(|n| (-720..=840).contains(n))
            .and_then(|n| i32::try_from(n).ok())
            .ok_or_else(|| format!("{FILE}: tz_offset_min must be -720..840"))?;
    }
    Ok(l)
}

/// The first millisecond of the day `now_ms` falls in, in the offset.
pub fn day_start_ms(now_ms: i64, tz_offset_min: i32) -> i64 {
    let off = i64::from(tz_offset_min) * 60_000;
    (now_ms + off).div_euclid(86_400_000) * 86_400_000 - off
}

/// What the budget says now.
#[derive(Debug, Clone, PartialEq)]
pub struct Answer {
    pub day_used: u32,
    pub card_hour_used: u32,
    pub day_tokens: u64,
    pub allowed: bool,
    pub why: Option<&'static str>,
    pub day_resets_at_ms: i64,
}

/// A push that may have cost something: sent, still in line, or maybe sent.
fn spent(v: &Value) -> bool {
    v["kind"] == SEND
        && v["action"] == PUSH_ACTION
        && matches!(v["outcome"].as_str(), Some("sent" | "queued" | "unknown"))
}

fn at_ms(v: &Value) -> Option<i64> {
    aib_ledger::envelope::parse_rfc3339_ms(v["at"].as_str()?)
}

/// Count `evs` (drive_send events, any order) against `l` at `now_ms`, for `card` when given.
pub fn answer(evs: &[Value], l: Limits, card: Option<&str>, now_ms: i64) -> Answer {
    let start = day_start_ms(now_ms, l.tz_offset_min);
    let hour = now_ms - 3_600_000;
    let mut day_used = 0u32;
    let mut card_hour_used = 0u32;
    let mut day_tokens = 0u64;
    for v in evs.iter().filter(|v| spent(v)) {
        let Some(t) = at_ms(v) else { continue };
        if t < start || t > now_ms {
            continue;
        }
        day_used = day_used.saturating_add(1);
        day_tokens = day_tokens.saturating_add(v["tokens"].as_u64().unwrap_or(0));
        let on_card = card.is_some_and(|c| {
            v["cards"]
                .as_array()
                .is_some_and(|a| a.iter().any(|x| x.as_str() == Some(c)))
        });
        if on_card && t > hour {
            card_hour_used = card_hour_used.saturating_add(1);
        }
    }
    let why = if day_used >= l.per_day {
        Some("day_full")
    } else if card.is_some() && card_hour_used >= l.per_card_hour {
        Some("card_hour_full")
    } else {
        None
    };
    Answer {
        day_used,
        card_hour_used,
        day_tokens,
        allowed: why.is_none(),
        why,
        day_resets_at_ms: start + 86_400_000,
    }
}

/// drive's events from the month of `now_ms` and of the day's start (a day can begin in the
/// month before); lines that are not JSON are skipped.
pub fn read_events(root: &Path, now_ms: i64, l: Limits) -> Vec<Value> {
    let d = events::dir(root, PROG);
    let mut names = vec![events::month_file(now_ms)];
    let first = events::month_file(day_start_ms(now_ms, l.tz_offset_min));
    if !names.contains(&first) {
        names.push(first);
    }
    names
        .iter()
        .filter_map(|n| std::fs::read_to_string(d.join(n)).ok())
        .flat_map(|t| {
            t.lines()
                .filter_map(|l| serde_json::from_str::<Value>(l).ok())
                .collect::<Vec<_>>()
        })
        .collect()
}

/// `aib-drive budget [--card ID] [--json]`: rc 0 with the answer (allowed or not), 2 usage,
/// 3 no state folder.
pub fn cli(raw: &[String], root: Option<&Path>, now_ms: i64) -> (i32, String, String) {
    let mut card: Option<String> = None;
    let mut as_json = false;
    let mut it = raw.iter();
    while let Some(a) = it.next() {
        match a.as_str() {
            "--json" => as_json = true,
            "--card" => match it.next() {
                Some(c) if !c.trim().is_empty() && c.len() <= 64 => card = Some(c.clone()),
                _ => {
                    return (
                        2,
                        String::new(),
                        "aib-drive budget: --card needs an id\n".into(),
                    )
                }
            },
            _ => {
                return (
                    2,
                    String::new(),
                    "usage: aib-drive budget [--card ID] [--json]\n".into(),
                )
            }
        }
    }
    let Some(root) = root else {
        return (
            3,
            String::new(),
            "aib-drive budget: no state folder\n".into(),
        );
    };
    let mut err = String::new();
    let l = match std::fs::read_to_string(root.join("drive").join(FILE)) {
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Limits::default(),
        Err(e) => {
            err.push_str(&format!("aib-drive budget: {FILE}: {e} (defaults used)\n"));
            Limits::default()
        }
        Ok(t) => parse_limits(&t).unwrap_or_else(|e| {
            err.push_str(&format!("aib-drive budget: {e} (defaults used)\n"));
            Limits::default()
        }),
    };
    let a = answer(&read_events(root, now_ms, l), l, card.as_deref(), now_ms);
    let out = if as_json {
        json!({
            "allowed": a.allowed, "why": a.why,
            "day_used": a.day_used, "day_max": l.per_day,
            "card": card, "card_hour_used": a.card_hour_used, "card_hour_max": l.per_card_hour,
            "run_timeout_min": l.run_timeout_min, "day_tokens": a.day_tokens,
            "day_resets_at": crate::act::rfc3339_ms(a.day_resets_at_ms),
        })
        .to_string()
            + "\n"
    } else {
        format!(
            "AI 推進:今天 {}/{}{}{}\n",
            a.day_used,
            l.per_day,
            card.as_deref().map_or(String::new(), |c| format!(
                ",{c} 這一小時 {}/{}",
                a.card_hour_used, l.per_card_hour
            )),
            match a.why {
                None => ",可以推".to_owned(),
                Some("day_full") => ",今天用完了".to_owned(),
                Some(_) => ",這張卡這一小時推過了".to_owned(),
            }
        )
    };
    (0, out, err)
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;

    /// 2026-11-17T14:00:00Z = 22:00 Taipei.
    const NOW: i64 = 1_794_924_000_000;

    fn ev(action: &str, outcome: &str, ago_min: i64, cards: &[&str]) -> Value {
        json!({"kind": SEND, "action": action, "outcome": outcome,
               "at": crate::act::rfc3339_ms(NOW - ago_min * 60_000), "cards": cards, "tokens": 1000})
    }

    #[test]
    fn the_day_is_the_day_in_the_offset() {
        // 22:00 Taipei: the day began at 16:00Z the day before
        assert_eq!(day_start_ms(NOW, 480), NOW - 22 * 3_600_000);
        assert_eq!(day_start_ms(NOW, 0), NOW - 14 * 3_600_000);
        assert_eq!(day_start_ms(NOW, -240), NOW - 10 * 3_600_000);
    }

    /// a1: a push sent, queued or maybe sent today counts; act's own sends, held / not sent,
    /// and yesterday's do not.
    #[test]
    fn only_todays_pushes_that_may_have_cost_count() {
        let evs = vec![
            ev("push", "sent", 10, &["S3"]),
            ev("push", "queued", 120, &["S4"]),
            ev("push", "unknown", 300, &[]),
            ev("push", "held", 5, &["S3"]),
            ev("push", "not_sent", 5, &["S3"]),
            ev("nudge", "sent", 5, &["S3"]),
            ev("push", "sent", 23 * 60, &["S3"]),
        ];
        let a = answer(&evs, Limits::default(), Some("S3"), NOW);
        assert_eq!(a.day_used, 3);
        assert_eq!(a.day_tokens, 3000, "tokens added up, never a limit");
        assert_eq!(a.card_hour_used, 1);
        assert!(!a.allowed);
        assert_eq!(a.why, Some("card_hour_full"));
        let other = answer(&evs, Limits::default(), Some("S9"), NOW);
        assert!(other.allowed, "{other:?}");
        assert_eq!(other.day_resets_at_ms, NOW + 2 * 3_600_000);
    }

    #[test]
    fn a_full_day_stops_every_card() {
        let evs: Vec<Value> = (0..20)
            .map(|i| ev("push", "sent", 70 + i, &["S1"]))
            .collect();
        let a = answer(&evs, Limits::default(), Some("S2"), NOW);
        assert_eq!((a.allowed, a.why), (false, Some("day_full")));
        let l = Limits {
            per_day: 21,
            ..Limits::default()
        };
        assert!(answer(&evs, l, Some("S2"), NOW).allowed);
    }

    #[test]
    fn settings_keep_defaults_for_missing_keys_and_refuse_bad_values() {
        let l = parse_limits(r#"{"per_day": 5, "tz_offset_min": -300}"#).unwrap();
        assert_eq!(
            l,
            Limits {
                per_day: 5,
                tz_offset_min: -300,
                ..Limits::default()
            }
        );
        for bad in [
            r#"{"per_day": 0}"#,
            r#"{"per_card_hour": -1}"#,
            r#"{"run_timeout_min": "30"}"#,
            r#"{"tz_offset_min": 900}"#,
            "not json",
        ] {
            assert!(parse_limits(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn the_command_reads_the_events_and_the_settings() {
        let t = crate::act::tests::Scratch::new("budget");
        let d = events::dir(t.path(), PROG);
        std::fs::create_dir_all(&d).unwrap();
        let lines: String = [
            ev("push", "sent", 10, &["S3"]),
            ev("push", "sent", 90, &["S3"]),
        ]
        .iter()
        .map(|v| v.to_string() + "\n")
        .collect();
        std::fs::write(d.join(events::month_file(NOW)), lines).unwrap();
        let (rc, out, err) = cli(
            &["--card".into(), "S3".into(), "--json".into()],
            Some(t.path()),
            NOW,
        );
        assert_eq!(rc, 0, "{err}");
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(v["day_used"], 2);
        assert_eq!(v["allowed"], false);
        assert_eq!(v["why"], "card_hour_full");
        std::fs::create_dir_all(t.path().join("drive")).unwrap();
        std::fs::write(t.path().join("drive").join(FILE), r#"{"per_card_hour": 2}"#).unwrap();
        let (_, out, _) = cli(
            &["--card".into(), "S3".into(), "--json".into()],
            Some(t.path()),
            NOW,
        );
        let v: Value = serde_json::from_str(&out).unwrap();
        assert_eq!(
            (v["allowed"].clone(), v["card_hour_max"].clone()),
            (json!(true), json!(2))
        );
        std::fs::write(t.path().join("drive").join(FILE), r#"{"per_day": 0}"#).unwrap();
        let (rc, _, err) = cli(&[], Some(t.path()), NOW);
        assert_eq!(rc, 0);
        assert!(err.contains("defaults used"), "{err}");
        assert_eq!(cli(&["--card".into()], Some(t.path()), NOW).0, 2);
        assert_eq!(cli(&[], None, NOW).0, 3);
    }
}
