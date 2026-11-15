//! The event envelope of a ledger line (spec V1.1 §8.2; Andy 11-14 20:46 v7, S97).
//!
//! - v 1 (every line written before S97): `v`, `ev`, `event_id`, and the time as `at_ms`
//!   (milliseconds since 1970). Read as it is; old lines are never rewritten.
//! - v 2 (every line written from S97): `v`, `ev`, `event_id`, the time as `occurred_at`
//!   (RFC 3339, UTC, `Z`, milliseconds: `2026-11-14T12:34:56.789Z`, exactly that shape), and
//!   `aggregate_type` / `aggregate_id` / `sequence` (a9136 spec M4: what aibd's `events`
//!   table keys on, so an import run ten times gives the same rows as once). A v 2 line
//!   carries no `at_ms`: one clock per line.
//!
//! `sequence` is the line's place among the ledger's events, from 1, v 1 lines included
//! (they get theirs from where they are). A v 2 line whose number is not the next one is
//! refused: a line lost, doubled or moved shows instead of being guessed around.
//!
//! The crate still knows no event types: the caller names its aggregate and reads the rest.

use serde_json::{Map, Value};

/// The version every new line is written with.
pub const V_WRITE: i64 = 2;

/// The one aggregate a ledger's lines belong to (a tool's whole ledger is one stream).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Aggregate<'a> {
    pub kind: &'a str,
    pub id: &'a str,
}

/// What every line says about itself, whichever version wrote it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Head {
    pub v: i64,
    pub ev: String,
    pub event_id: String,
    /// milliseconds since 1970, UTC
    pub at_ms: i64,
    pub sequence: u64,
}

/// Read the envelope of one line. `next` is the sequence this line must have (events read
/// so far + 1). Errors are short words for `line <n>: …`.
pub fn read(o: &Map<String, Value>, agg: Aggregate<'_>, next: u64) -> Result<Head, String> {
    let v = o.get("v").and_then(Value::as_i64);
    let ev = match o.get("ev").and_then(Value::as_str) {
        Some(s) if !s.is_empty() => s.to_owned(),
        _ => return Err("bad ev".into()),
    };
    let event_id = match o.get("event_id").and_then(Value::as_str) {
        Some(s) if !s.is_empty() => s.to_owned(),
        _ => return Err("bad event_id".into()),
    };
    let at_ms = match v {
        Some(1) => o.get("at_ms").and_then(Value::as_i64).ok_or("bad at_ms")?,
        Some(2) => {
            if o.contains_key("at_ms") {
                return Err("a v 2 line with at_ms".into());
            }
            if o.get("aggregate_type").and_then(Value::as_str) != Some(agg.kind)
                || o.get("aggregate_id").and_then(Value::as_str) != Some(agg.id)
            {
                return Err("bad aggregate".into());
            }
            if o.get("sequence").and_then(Value::as_u64) != Some(next) {
                return Err(format!("sequence is not {next}"));
            }
            o.get("occurred_at")
                .and_then(Value::as_str)
                .and_then(parse_rfc3339_ms)
                .ok_or("bad occurred_at")?
        }
        _ => return Err("unknown v".into()),
    };
    Ok(Head {
        v: v.unwrap_or_default(),
        ev,
        event_id,
        at_ms,
        sequence: next,
    })
}

/// The envelope of a new (v 2) line; the caller adds its own fields.
pub fn write(
    ev: &str,
    event_id: &str,
    at_ms: i64,
    agg: Aggregate<'_>,
    sequence: u64,
) -> Map<String, Value> {
    let mut o = Map::new();
    o.insert("v".into(), V_WRITE.into());
    o.insert("ev".into(), ev.into());
    o.insert("event_id".into(), event_id.into());
    o.insert("occurred_at".into(), rfc3339_ms(at_ms).into());
    o.insert("aggregate_type".into(), agg.kind.into());
    o.insert("aggregate_id".into(), agg.id.into());
    o.insert("sequence".into(), sequence.into());
    o
}

/// Days since 1970-01-01 of a civil date (proleptic Gregorian).
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// The civil date of a day count since 1970-01-01.
fn civil_from_days(z: i64) -> (i64, i64, i64) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (
        if m <= 2 {
            yoe + era * 400 + 1
        } else {
            yoe + era * 400
        },
        m,
        d,
    )
}

/// `2026-11-14T12:34:56.789Z` for milliseconds since 1970 (years 0000–9999; outside that the
/// year is clamped, which no clock of ours reaches).
pub fn rfc3339_ms(ms: i64) -> String {
    let days = ms.div_euclid(86_400_000);
    let rest = ms.rem_euclid(86_400_000);
    let (y, m, d) = civil_from_days(days);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:03}Z",
        y.clamp(0, 9999),
        m,
        d,
        rest / 3_600_000,
        rest / 60_000 % 60,
        rest / 1000 % 60,
        rest % 1000
    )
}

/// The milliseconds of exactly `YYYY-MM-DDTHH:MM:SS.mmmZ` (24 ASCII characters, a real date,
/// no leap second). Anything else is None: one shape, so two writers never disagree.
pub fn parse_rfc3339_ms(s: &str) -> Option<i64> {
    let b = s.as_bytes();
    if b.len() != 24 {
        return None;
    }
    for (i, want) in [
        (4, b'-'),
        (7, b'-'),
        (10, b'T'),
        (13, b':'),
        (16, b':'),
        (19, b'.'),
        (23, b'Z'),
    ] {
        if b[i] != want {
            return None;
        }
    }
    let num = |from: usize, to: usize| -> Option<i64> {
        let part = &b[from..to];
        if !part.iter().all(u8::is_ascii_digit) {
            return None;
        }
        part.iter().try_fold(0_i64, |n, d| {
            n.checked_mul(10)?.checked_add(i64::from(d - b'0'))
        })
    };
    let (y, mo, d) = (num(0, 4)?, num(5, 7)?, num(8, 10)?);
    let (h, mi, sec, ms) = (num(11, 13)?, num(14, 16)?, num(17, 19)?, num(20, 23)?);
    if !(1..=12).contains(&mo) || h > 23 || mi > 59 || sec > 59 {
        return None;
    }
    let leap = y % 4 == 0 && (y % 100 != 0 || y % 400 == 0);
    let last = match mo {
        2 if leap => 29,
        2 => 28,
        4 | 6 | 9 | 11 => 30,
        _ => 31,
    };
    if !(1..=last).contains(&d) {
        return None;
    }
    Some(days_from_civil(y, mo, d) * 86_400_000 + h * 3_600_000 + mi * 60_000 + sec * 1000 + ms)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    const AGG: Aggregate<'static> = Aggregate {
        kind: "idea_ledger",
        id: "ideas",
    };

    fn obj(v: Value) -> Map<String, Value> {
        match v {
            Value::Object(o) => o,
            _ => unreachable!("test literal is an object"),
        }
    }

    #[test]
    fn the_time_is_written_and_read_in_one_shape() {
        assert_eq!(rfc3339_ms(0), "1970-01-01T00:00:00.000Z");
        // the first line of the real ledger (at_ms 1794644140148)
        assert_eq!(rfc3339_ms(1_794_644_140_148), "2026-11-14T08:15:40.148Z");
        assert_eq!(
            parse_rfc3339_ms("2026-11-14T08:15:40.148Z"),
            Some(1_794_644_140_148)
        );
        assert_eq!(rfc3339_ms(951_782_400_000), "2000-02-29T00:00:00.000Z");
        for ms in [
            0,
            1,
            999,
            86_399_999,
            951_782_400_000,
            1_794_644_140_148,
            4_102_444_799_999,
        ] {
            assert_eq!(parse_rfc3339_ms(&rfc3339_ms(ms)), Some(ms), "{ms}");
        }
        for bad in [
            "2026-11-14T08:15:40Z",
            "2026-11-14T08:15:40.148+08:00",
            "2026-11-14 08:15:40.148Z",
            "2026-11-14T08:15:40.1480Z",
            "2026-02-29T00:00:00.000Z",
            "2100-02-29T00:00:00.000Z",
            "2026-13-01T00:00:00.000Z",
            "2026-11-31T00:00:00.000Z",
            "2026-11-14T24:00:00.000Z",
            "2026-11-14T08:15:60.000Z",
            "2026-11-14T08:15:40.14xZ",
            "２026-11-14T08:15:40.148Z",
            "",
        ] {
            assert_eq!(parse_rfc3339_ms(bad), None, "{bad}");
        }
        assert_eq!(
            parse_rfc3339_ms("2024-02-29T00:00:00.000Z"),
            Some(1_709_164_800_000)
        );
    }

    #[test]
    fn a_v1_line_is_read_as_it_is_and_takes_its_place_as_its_sequence() {
        let o = obj(json!({"v": 1, "ev": "add", "event_id": "e1", "at_ms": 1_794_644_140_148_i64}));
        assert_eq!(
            read(&o, AGG, 7),
            Ok(Head {
                v: 1,
                ev: "add".into(),
                event_id: "e1".into(),
                at_ms: 1_794_644_140_148,
                sequence: 7
            })
        );
        let o = obj(
            json!({"v": 1, "ev": "add", "event_id": "e1", "occurred_at": "2026-11-14T08:15:40.148Z"}),
        );
        assert_eq!(read(&o, AGG, 1), Err("bad at_ms".into()), "v 1 keeps at_ms");
    }

    #[test]
    fn a_v2_line_reads_back_what_was_written_and_every_field_is_checked() {
        let w = write("add", "e2", 1_794_644_140_148, AGG, 3);
        assert_eq!(w["occurred_at"], "2026-11-14T08:15:40.148Z");
        assert!(!w.contains_key("at_ms"));
        assert_eq!(
            read(&w, AGG, 3),
            Ok(Head {
                v: 2,
                ev: "add".into(),
                event_id: "e2".into(),
                at_ms: 1_794_644_140_148,
                sequence: 3
            })
        );
        assert_eq!(read(&w, AGG, 4), Err("sequence is not 4".into()));
        let other = Aggregate {
            kind: "idea_ledger",
            id: "other",
        };
        assert_eq!(read(&w, other, 3), Err("bad aggregate".into()));
        let mut two_clocks = w.clone();
        two_clocks.insert("at_ms".into(), 1.into());
        assert_eq!(
            read(&two_clocks, AGG, 3),
            Err("a v 2 line with at_ms".into())
        );
        let mut bad_time = w.clone();
        bad_time.insert("occurred_at".into(), "2026-11-14T08:15:40Z".into());
        assert_eq!(read(&bad_time, AGG, 3), Err("bad occurred_at".into()));
        let mut v3 = w;
        v3.insert("v".into(), 3.into());
        assert_eq!(read(&v3, AGG, 3), Err("unknown v".into()));
    }
}
