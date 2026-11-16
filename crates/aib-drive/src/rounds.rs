//! S79-own o5 (Andy 11-16 11:0x 「每輪記一筆,也要寫成 rust 必要記錄可查」,
//! DECISION-andy-519d0-1116-0530.md §19): one line a round in `<root>/drive/rounds.jsonl`,
//! and the log a person reads.
//!
//! - `aib-drive round done` writes the line: how many messages came in and went out, the own
//!   item done (or `none` with why), and who started the turn — 自動推進 (a timed prompt) or
//!   真人, read from the transcript the turn-start hook named (aib-own-gate), or given with
//!   `--trigger`. A round with nothing done is written too.
//! - `aib-drive round log` lists them, one line a round; rounds whose starter cannot be told
//!   are listed as 分不出來 and counted.
//!
//! 真人 means the turn came in through the input box, and that includes a seat's dm (aib dm
//! types into the box, so the transcript says human); only a timed prompt is 自動推進
//! (1ced7 o5 L1). A round with no turn start is its own stream (`<seat>/ev:<event_id>`), so
//! the same work written twice that way is not caught (N1).
//!
//! Who started a turn is bookkeeping, not a security line: the same account can write the
//! transcript (GUIDE-same-user-ai-threat-model).
use aib_ledger::envelope::{self, Aggregate};
use aib_own_gate::{TRIGGER_AUTO, TRIGGER_HUMAN, TRIGGER_UNKNOWN};
use serde_json::{json, Value};
use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::sync::atomic::{AtomicU64, Ordering};

/// The round ledger, under `<root>/drive` (aib-own-gate reads the same name).
pub const LEDGER: &str = aib_own_gate::ROUNDS;
/// Line schema: the aib-ledger envelope v 2 (IFACE-ledger-envelope-b433b-1117 r3 §6–§7): `v`, `ev`,
/// `event_id`, `occurred_at` (no `at_ms`), `aggregate_type` / `aggregate_id` / `sequence`.
pub const V: i64 = envelope::V_WRITE;
/// `ev` of a round line.
pub const EV: &str = "round";
/// `aggregate_type` of a round line. One stream a turn (`<seat>/<turn_id>`, or `<seat>/ev:<event_id>`
/// for a round with no turn start), so every round line is `sequence` 1 and two writers never race.
pub const AGG: &str = "drive.rounds";
/// `--own` for a round with no own item done.
pub const OWN_NONE: &str = "none";
/// How long `--own` and `--why` may be.
pub const TEXT_MAX: usize = 300;
/// The log shows times at this offset from UTC (the cabin and Andy are in Taipei).
pub const SHOW_OFFSET_MIN: i64 = 8 * 60;
pub const RC_OK: i32 = 0;
pub const RC_REFUSED: i32 = 1;
pub const RC_USAGE: i32 = 2;
pub const RC_WRITE: i32 = 3;

/// What `round done` was given.
#[derive(Debug, Default, Clone)]
pub struct DoneReq<'a> {
    pub seat: &'a str,
    pub recv: Option<&'a str>,
    pub sent: Option<&'a str>,
    pub own: Option<&'a str>,
    pub why: Option<&'a str>,
    pub dm_in: Option<&'a str>,
    pub dm_out: Option<&'a str>,
    /// `--trigger`: 自動推進 or 真人; wins over the transcript.
    pub trigger: Option<&'a str>,
    /// The line's `event_id` (uuid shape; [`new_event_id`] for a live round).
    pub event_id: &'a str,
    pub at_ms: i64,
}

/// The seat's current turn from the own ledger (aib-own-gate's turn_start): its id and,
/// when the hook gave one, who started it as read from the transcript.
#[derive(Debug, Default, Clone)]
pub struct Turn {
    pub id: String,
    pub start_ms: i64,
    /// `None` = no transcript to read.
    pub trigger: Option<&'static str>,
}

fn count(k: &str, v: Option<&str>) -> Result<u64, String> {
    let v = v.ok_or_else(|| format!("--{k} 沒給(幾封;沒有就寫 0)"))?;
    if v.is_empty() || v.len() > 6 || !v.bytes().all(|b| b.is_ascii_digit()) {
        return Err(format!("--{k} 只收 0–999999 的半形數字:{v}"));
    }
    v.parse().map_err(|_| format!("--{k} {v}"))
}

fn text(k: &str, v: &str) -> Result<String, String> {
    let t = v.trim();
    if t.is_empty() {
        return Err(format!("--{k} 是空的"));
    }
    if t.chars().count() > TEXT_MAX || t.contains(['\n', '\r']) {
        return Err(format!("--{k} 要一行、{TEXT_MAX} 字內"));
    }
    Ok(t.to_owned())
}

/// A dm id list: comma separated `q_` + 16 lowercase hex; empty = none.
pub fn dm_ids(k: &str, v: Option<&str>) -> Result<Vec<String>, String> {
    let Some(v) = v.map(str::trim).filter(|v| !v.is_empty()) else {
        return Ok(Vec::new());
    };
    let mut out = Vec::new();
    for id in v.split(',').map(str::trim) {
        let ok = id.len() == 18
            && id.starts_with("q_")
            && id[2..]
                .bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase());
        if !ok {
            return Err(format!("--{k} 的 id 要像 q_0123456789abcdef:{id}"));
        }
        if !out.iter().any(|x| x == id) {
            out.push(id.to_owned());
        }
    }
    Ok(out)
}

/// The seat's current turn from the own ledger's text, `None` when it has no turn start.
/// `read_tail` reads a transcript path (its tail is enough).
pub fn current_turn(
    own_ledger: &str,
    seat: &str,
    read_tail: &dyn Fn(&str) -> Option<String>,
) -> Option<Turn> {
    let lines = aib_own_gate::parse_ledger(own_ledger);
    let id = aib_own_gate::current_turn(&lines, seat)?;
    let start_ms = aib_own_gate::current_turn_start_ms(&lines, seat).unwrap_or(0);
    let trigger = aib_own_gate::current_turn_transcript(&lines, seat)
        .and_then(|p| read_tail(&p))
        .map(|t| aib_own_gate::trigger_of(&t));
    Some(Turn {
        id,
        start_ms,
        trigger,
    })
}

/// The `round` line, checked; `rounds` is the round ledger's text (a turn is written once).
pub fn build_line(req: &DoneReq<'_>, turn: Option<&Turn>, rounds: &str) -> Result<Value, String> {
    if !crate::next_local::is_seat(req.seat) {
        return Err(format!("--seat 要 5 碼小寫 hex:{}", req.seat));
    }
    let recv = count("recv", req.recv)?;
    let sent = count("sent", req.sent)?;
    let own = text("own", req.own.unwrap_or("")).map_err(|_| {
        "--own 沒給(做完的 own 帳 id;這輪沒做就 --own none --why \"<原因>\")".to_owned()
    })?;
    let why = match (own == OWN_NONE, req.why) {
        (true, None) => return Err("--own none 要帶 --why \"<這輪為什麼沒做>\"".into()),
        (_, Some(w)) => Some(text("why", w)?),
        (false, None) => None,
    };
    let dm_in = dm_ids("dm-in", req.dm_in)?;
    let dm_out = dm_ids("dm-out", req.dm_out)?;
    let (trigger, src) = match (req.trigger, turn.and_then(|t| t.trigger)) {
        (Some(t), _) if t == TRIGGER_AUTO || t == TRIGGER_HUMAN => (t, "flag"),
        (Some(t), _) => {
            return Err(format!(
                "--trigger 只收「{TRIGGER_AUTO}」或「{TRIGGER_HUMAN}」:{t}(不給就讀對話檔)"
            ))
        }
        (None, Some(t)) => (t, "transcript"),
        (None, None) => (TRIGGER_UNKNOWN, "none"),
    };
    if !is_uuid(req.event_id) {
        return Err(format!("event_id 要是 uuid 形狀:{}", req.event_id));
    }
    let turn_id = turn.map(|t| t.id.as_str()).unwrap_or("");
    if !turn_id.is_empty()
        && aib_own_gate::parse_ledger(rounds)
            .iter()
            .any(|l| l["seat"] == req.seat && l["turn_id"] == turn_id && is_round(l))
    {
        return Err(format!("這一輪({turn_id})已經記過了;一輪一筆"));
    }
    let stream = if turn_id.is_empty() {
        format!("{}/ev:{}", req.seat, req.event_id)
    } else {
        format!("{}/{turn_id}", req.seat)
    };
    let agg = Aggregate {
        kind: AGG,
        id: &stream,
    };
    let mut m = envelope::write(EV, req.event_id, req.at_ms, agg, 1);
    let own_fields = json!({
        "seat": req.seat, "invocation_id": turn_id, "turn_id": turn_id,
        "trigger": trigger, "trigger_src": src,
        "recv": recv, "sent": sent, "own": own, "dm_in": dm_in, "dm_out": dm_out,
    });
    if let Value::Object(f) = own_fields {
        m.extend(f);
    }
    if let Some(w) = why {
        m.insert("why".into(), Value::from(w));
    }
    Ok(Value::Object(m))
}

/// A round line: v 2 names it in `ev`, the v 1 lines in `kind`; the hand-written ledger has
/// neither and is read as rounds by [`rows`].
pub fn is_round(l: &Value) -> bool {
    l["ev"] == EV || l["kind"] == EV
}

fn is_uuid(s: &str) -> bool {
    s.len() == 36
        && s.bytes().enumerate().all(|(i, b)| match i {
            8 | 13 | 18 | 23 => b == b'-',
            _ => b.is_ascii_digit() || (b'a'..=b'f').contains(&b),
        })
}

static SALT: AtomicU64 = AtomicU64::new(0);

/// A new uuid v4 for a live round line (std only: process id, clock and a counter through
/// the std random hasher; uniqueness, not secrecy, is what it is for).
pub fn new_event_id() -> String {
    let mut bits = 0u128;
    for _ in 0..2 {
        let mut h = RandomState::new().build_hasher();
        h.write_u32(std::process::id());
        h.write_u128(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos()),
        );
        h.write_u64(SALT.fetch_add(1, Ordering::Relaxed));
        bits = (bits << 64) | u128::from(h.finish());
    }
    let b = (bits & !(0xf << 76) | (0x4 << 76)) & !(0x3 << 62) | (0x2 << 62);
    let x = format!("{b:032x}");
    format!(
        "{}-{}-{}-{}-{}",
        &x[0..8],
        &x[8..12],
        &x[12..16],
        &x[16..20],
        &x[20..32]
    )
}

// ---- time ----

fn civil(days: i64) -> (i64, i64, i64) {
    // civil from days (Howard Hinnant)
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (yoe + era * 400 + i64::from(m <= 2), m, d)
}

fn parts(ms: i64) -> (i64, i64, i64, i64, i64, i64) {
    let secs = ms.div_euclid(1000);
    let (y, mo, d) = civil(secs.div_euclid(86_400));
    let s = secs.rem_euclid(86_400);
    (y, mo, d, s / 3600, s % 3600 / 60, s % 60)
}

/// `2026-11-16T03:04:05Z`.
pub fn utc_text(ms: i64) -> String {
    let (y, mo, d, h, mi, s) = parts(ms);
    format!("{y:04}-{mo:02}-{d:02}T{h:02}:{mi:02}:{s:02}Z")
}

/// `11-16 11:04` at [`SHOW_OFFSET_MIN`].
pub fn show_time(ms: i64) -> String {
    let (_, mo, d, h, mi, _) = parts(ms + SHOW_OFFSET_MIN * 60_000);
    format!("{mo:02}-{d:02} {h:02}:{mi:02}")
}

/// Milliseconds of an RFC 3339 time with `Z` or `±HH:MM`, seconds optional
/// (`2026-11-16T10:46+08:00` as the hand-written ledger has it).
pub fn parse_time(t: &str) -> Option<i64> {
    let t = t.trim();
    if let Some(ms) = aib_own_gate::iso_ms(t) {
        return Some(ms);
    }
    let (body, off) = if let Some(b) = t.strip_suffix('Z') {
        (b, 0)
    } else {
        let i = t.len().checked_sub(6)?;
        let (b, o) = (t.get(..i)?, t.get(i..)?);
        let sign = match o.as_bytes().first()? {
            b'+' => 1,
            b'-' => -1,
            _ => return None,
        };
        let (h, m) = o.get(1..)?.split_once(':')?;
        let num = |x: &str| {
            (x.len() == 2 && x.bytes().all(|c| c.is_ascii_digit()))
                .then(|| x.parse::<i64>().ok())
                .flatten()
        };
        (b, sign * (num(h)? * 60 + num(m)?))
    };
    let body = if body.len() == 16 {
        format!("{body}:00")
    } else {
        body.to_owned()
    };
    aib_own_gate::iso_ms(&format!("{body}Z")).map(|ms| ms - off * 60_000)
}

/// `--since`: `30m` / `24h` / `7d` back from `now_ms`, or an RFC 3339 time.
pub fn parse_since(v: &str, now_ms: i64) -> Option<i64> {
    let v = v.trim();
    let unit = match v.chars().last()? {
        'm' => 60_000,
        'h' => 3_600_000,
        'd' => 86_400_000,
        _ => return parse_time(v),
    };
    let n = &v[..v.len() - 1];
    if n.is_empty() || n.len() > 6 || !n.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    Some(now_ms - n.parse::<i64>().ok()? * unit)
}

// ---- log ----

/// A line's time: v 2 `occurred_at`, else v 1 `at_ms`, else its `at` text (the
/// hand-written ledger has only `at`).
pub fn at_ms(l: &Value) -> Option<i64> {
    l.get("occurred_at")
        .and_then(Value::as_str)
        .and_then(envelope::parse_rfc3339_ms)
        .or_else(|| l.get("at_ms").and_then(Value::as_i64))
        .or_else(|| l.get("at").and_then(Value::as_str).and_then(parse_time))
}

/// The seat's rounds at or after `since_ms`, oldest first. A line without a time is kept
/// (and shown without one) so nothing written disappears from the log.
pub fn rows(rounds: &str, seat: &str, since_ms: Option<i64>) -> Vec<Value> {
    let mut v: Vec<Value> = aib_own_gate::parse_ledger(rounds)
        .into_iter()
        .filter(|l| l["seat"] == seat)
        .filter(|l| {
            ["ev", "kind"]
                .iter()
                .all(|k| l.get(*k).and_then(Value::as_str).is_none_or(|v| v == EV))
        })
        .filter(|l| match (since_ms, at_ms(l)) {
            (Some(s), Some(t)) => t >= s,
            _ => true,
        })
        .collect();
    v.sort_by_key(|l| at_ms(l).unwrap_or(i64::MIN));
    v
}

fn show_trigger(t: &str) -> &str {
    match t {
        x if x == TRIGGER_AUTO || x == TRIGGER_HUMAN => x,
        _ => "分不出來",
    }
}

fn list_len(l: &Value, k: &str) -> usize {
    l.get(k).and_then(Value::as_array).map_or(0, Vec::len)
}

/// The log for a person: one line a round, then the counts.
pub fn render(seat: &str, rows: &[Value]) -> String {
    let mut s = format!("aib-drive round log {seat}({} 輪,時間為台北)\n", rows.len());
    let (mut auto, mut human, mut unknown, mut idle) = (0, 0, 0, 0);
    for l in rows {
        let trig = show_trigger(l["trigger"].as_str().unwrap_or(""));
        match trig {
            x if x == TRIGGER_AUTO => auto += 1,
            x if x == TRIGGER_HUMAN => human += 1,
            _ => unknown += 1,
        }
        let own = l["own"].as_str().unwrap_or("");
        let did = if own == OWN_NONE || own.is_empty() {
            idle += 1;
            format!("沒做({})", l["why"].as_str().unwrap_or("沒寫原因"))
        } else {
            own.to_owned()
        };
        let when = at_ms(l).map_or_else(|| "--:-- --:--".to_owned(), show_time);
        let dm = match (list_len(l, "dm_in"), list_len(l, "dm_out")) {
            (0, 0) => String::new(),
            (i, o) => format!(" · dm 入 {i} 出 {o}"),
        };
        s.push_str(&format!(
            "{when} {trig} 收 {} 發 {} · 做:{did}{dm}\n",
            l["recv"].as_u64().unwrap_or(0),
            l["sent"].as_u64().unwrap_or(0)
        ));
    }
    s.push_str(&format!(
        "合計:{TRIGGER_AUTO} {auto}、{TRIGGER_HUMAN} {human}(輸入框來的，含席位來信)、分不出來 {unknown};沒做事的 {idle} 輪\n"
    ));
    s
}
