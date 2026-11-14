// SPDX-License-Identifier: Apache-2.0
//! The title's pure pieces, from `title/title_beat.py` (sampler to Rust, S1;
//! PLAN-aibtitle-rust-sampler-043dd-1113). Only what `title_sample.py` calls:
//! `trust_light`, `seat_record`, `seat_light`, `cabin_label`, `cabin_since`, `uptime_str`,
//! `title_string`, `model_label`, `short_version`, `version_segment`, `version_state`.
//!
//! Same rule as the Python module: no clock, no files, no environment. Every input (now,
//! pid liveness, transcript age, the seat's `@` label, local time) comes from the caller,
//! so one input always gives one title. The Python stays frozen (Andy 11-13 「程式只能是
//! rust 沒有 Python」); tests/beat_golden.rs compares these functions with a table the
//! frozen Python produced, string for string.
//!
//! Where Python and Rust text rules differ (`str.strip`, `str.lower`, `capitalize`,
//! `isdigit`, `isalpha`), the helpers below follow Python for everything a seat file or a
//! model id holds in practice (ASCII, CJK, emoji); the few Unicode corners they do not
//! copy are named next to each helper.

use serde_json::{Map, Value};

/// Heartbeat younger than this is fresh (ms).
pub const FRESH_MS: i64 = 15 * 60 * 1000;
/// Heartbeat older than this is stale (ms).
pub const STALE_MS: i64 = 60 * 60 * 1000;
/// The four beat frames, cycled by the beat sequence.
pub const BEAT_FRAMES: [&str; 4] = ["·", ":", "∴", ":"];
/// What `cabin_label` gives when there is no cabin id.
pub const NO_CABIN: &str = "no-cabin";
/// The schema a seat file must carry.
pub const SEAT_SCHEMA: &str = "aib-seat/v1";
/// Rank marks the right pane may hand in (`rank_hint`), the only ones taken.
pub const RANK_HINTS: [&str; 6] = ["👑", "⚓", "🎓", "❓", "🤖", "🔑"];
/// Root seats on these providers are API-key seats: 🔑, not ⚓.
pub const API_KEY_PROVIDERS: [&str; 5] = [
    "deepseek",
    "nvidia",
    "openrouter",
    "typesafe",
    "openrouterjev",
];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Level {
    Red,
    Yellow,
    Green,
}

impl Level {
    pub fn as_str(self) -> &'static str {
        match self {
            Level::Red => "red",
            Level::Yellow => "yellow",
            Level::Green => "green",
        }
    }
    /// The shape used where colour is not shown (tmux titles): hollow / half / full.
    fn mono(self) -> &'static str {
        match self {
            Level::Red => "○",
            Level::Yellow => "◐",
            Level::Green => "●",
        }
    }
    fn ansi(self) -> &'static str {
        match self {
            Level::Red => "\x1b[31m",
            Level::Yellow => "\x1b[33m",
            Level::Green => "\x1b[32m",
        }
    }
}

/// One light: level, a fixed code, a line for people.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Light {
    pub level: Level,
    pub code: &'static str,
    pub detail: String,
}

fn light(level: Level, code: &'static str, detail: impl Into<String>) -> Light {
    Light {
        level,
        code,
        detail: detail.into(),
    }
}

/// Python `str.isspace()` for one char: Unicode White_Space plus U+001C..U+001F.
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// Python `str.strip()`.
fn py_strip(s: &str) -> &str {
    s.trim_matches(py_space)
}

/// Python `str.capitalize()`: the first char upper, the rest lower. Python title-cases
/// the first char; the two differ only for the four digraph letters (U+01C4..U+01CC,
/// U+01F1..U+01F3), which no provider name holds.
fn py_capitalize(s: &str) -> String {
    let mut cs = s.chars();
    match cs.next() {
        None => String::new(),
        Some(f) => f
            .to_uppercase()
            .chain(cs.flat_map(char::to_lowercase))
            .collect(),
    }
}

/// Python `str.isdigit()` on a whole string (false when empty), ASCII digits only: Python
/// also takes other Unicode digits (`٣`, `²`), which a model id never holds.
fn py_isdigit(s: &str) -> bool {
    !s.is_empty() && s.chars().all(|c| c.is_ascii_digit())
}

/// Python truthiness of a JSON value.
fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// Python `"%s" % v` for the JSON values a seat file's `trust` can hold.
fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        Value::Null => "None".into(),
        other => other.to_string(),
    }
}

/// Python `int(x)` for a float: toward zero.
fn py_int(x: f64) -> i64 {
    x.trunc() as i64
}

/// The registry record `trust_light` reads (only the two fields it looks at).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct TrustRecord {
    pub beat_epoch: Option<i64>,
    pub registry_verified: Option<bool>,
}

/// The registry light (trust_light). `None` or an empty record is red; the sampler calls
/// it with no record when there is no seat file.
pub fn trust_light(record: Option<&TrustRecord>, bind_status: Option<&str>, now_ms: i64) -> Light {
    let Some(r) = record else {
        return light(Level::Red, "no_registry_record", "registry 查無此 session");
    };
    let beat = r.beat_epoch.unwrap_or(0);
    if beat == 0 {
        return light(Level::Red, "no_heartbeat", "記錄裡沒有心跳");
    }
    let age_ms = now_ms - beat * 1000;
    if age_ms > STALE_MS {
        return light(
            Level::Red,
            "heartbeat_stale",
            format!("心跳已 {} 分鐘沒動", age_ms.div_euclid(60000)),
        );
    }
    match r.registry_verified {
        None => match bind_status {
            Some("approved_pending_ea1") => light(
                Level::Yellow,
                "approved_pending_ea1",
                "人已核可,真綁候 EA.1",
            ),
            Some("pending") => light(Level::Red, "bind_pending", "綁定申請候人核可"),
            _ => light(
                Level::Red,
                "registry_field_absent",
                "registry 沒有任何綁定欄位,視同未綁",
            ),
        },
        Some(false) => light(Level::Red, "not_registry_verified", "registry 驗證未通過"),
        Some(true) if age_ms > FRESH_MS => light(
            Level::Yellow,
            "verified_but_aging",
            format!("已驗證,但心跳 {} 分鐘沒動", age_ms.div_euclid(60000)),
        ),
        Some(true) => light(Level::Green, "verified_fresh", "已驗證且心跳新鮮"),
    }
}

/// The beat frame for a sequence number (Python `%` never goes negative).
pub fn beat_glyph(beat_seq: i64) -> &'static str {
    BEAT_FRAMES[beat_seq.rem_euclid(4) as usize]
}

/// What `seat_record` gives: the signature and what `seat_light` needs.
#[derive(Clone, Debug, PartialEq)]
pub struct SeatRecord {
    pub sig: String,
    /// The seat file's `trust` as it is (any JSON value, or absent).
    pub seat_trust: Option<Value>,
    pub beat_epoch: Option<i64>,
}

/// The inputs of `seat_record` besides the seat file.
#[derive(Clone, Copy, Debug, Default)]
pub struct SeatSignals<'a> {
    /// `seat_address.label_of(seat)` as the caller got it (`@` + five chars, or something
    /// with `?` when it could not tell). Only a clean `@…` replaces the seat id.
    pub label: Option<&'a str>,
    pub pid_alive: bool,
    pub transcript_age_s: Option<f64>,
    pub now_s: Option<f64>,
    pub persona: Option<&'a str>,
    pub rank_hint: Option<&'a str>,
    pub model: Option<&'a str>,
}

fn str_field<'a>(seat: &'a Map<String, Value>, k: &str) -> Option<&'a str> {
    seat.get(k).and_then(Value::as_str)
}

/// A seat file to the record `seat_light` reads (seat_record); None when it is not an
/// `aib-seat/v1` file with a seat id.
pub fn seat_record(seat: &Value, s: &SeatSignals<'_>) -> Option<SeatRecord> {
    let seat = seat.as_object()?;
    if str_field(seat, "schema") != Some(SEAT_SCHEMA) {
        return None;
    }
    let mut sid = str_field(seat, "seat_id").filter(|v| !v.is_empty())?;
    if let Some(l) = s
        .label
        .and_then(|l| l.strip_prefix('@'))
        .filter(|l| !l.contains('?'))
    {
        sid = l;
    }
    let role = str_field(seat, "role").unwrap_or("?");
    let provider = str_field(seat, "provider").unwrap_or("?");
    let rank = if let Some(h) = s.rank_hint.filter(|h| RANK_HINTS.contains(h)) {
        format!("{h} ")
    } else if seat.get("advisor") == Some(&Value::Bool(true)) {
        "🎓 ".into()
    } else if seat.get("captain_assigned") == Some(&Value::Bool(true)) {
        "👑 ".into()
    } else if role == "root" {
        // `(seat.get("provider") or "").strip().lower()`
        let p = str_field(seat, "provider")
            .map(|p| py_strip(p).to_lowercase())
            .unwrap_or_default();
        if API_KEY_PROVIDERS.contains(&p.as_str()) {
            "🔑 ".into()
        } else {
            "⚓ ".into()
        }
    } else {
        String::new()
    };
    let who = s
        .persona
        .filter(|p| !p.is_empty() && !p.contains('@') && !p.contains('|'))
        .unwrap_or(role);
    let m = model_label(s.model);
    let model_part = if m.is_empty() {
        String::new()
    } else {
        format!("/{m}")
    };
    let mut rec = SeatRecord {
        sig: format!("{rank}{who}@{sid}/{}{model_part}", py_capitalize(provider)),
        seat_trust: seat.get("trust").cloned(),
        beat_epoch: None,
    };
    let Some(now) = s.now_s else {
        return Some(rec);
    };
    if s.pid_alive {
        rec.beat_epoch = Some(py_int(now));
    } else if let Some(age) = s.transcript_age_s {
        rec.beat_epoch = Some(py_int(now - age.max(0.0)));
    }
    Some(rec)
}

/// The seat light (seat_light): at most yellow, since a seat file is self-reported.
pub fn seat_light(rec: Option<&SeatRecord>, now_ms: i64) -> Light {
    let Some(r) = rec.filter(|r| !r.sig.is_empty()) else {
        return light(Level::Red, "no_seat_record", "席位檔認不出來");
    };
    let beat = r.beat_epoch.unwrap_or(0);
    if beat == 0 {
        return light(
            Level::Red,
            "seat_no_signal",
            "席位檔在,但 pid 不存在且 transcript 未更新",
        );
    }
    if now_ms - beat * 1000 > STALE_MS {
        return light(Level::Red, "seat_stale", "最後活動已超過門檻");
    }
    let trust = r
        .seat_trust
        .as_ref()
        .filter(|v| truthy(Some(v)))
        .map(py_str)
        .unwrap_or_else(|| "?".into());
    light(
        Level::Yellow,
        "seat_live",
        format!("席位檔可讀且行程活著;trust={trust},無第二方背書"),
    )
}

/// The cabin's four-character code: after the last `-`, at most the last four chars
/// (cabin_label).
pub fn cabin_label(instance: Option<&str>) -> String {
    let Some(inst) = instance.map(py_strip).filter(|s| !s.is_empty()) else {
        return NO_CABIN.into();
    };
    let tail = inst.rsplit('-').next().unwrap_or(inst);
    let n = tail.chars().count();
    tail.chars().skip(n.saturating_sub(4)).collect()
}

/// How long the cabin has run, two largest units (uptime_str); empty when unknown or
/// in the future.
pub fn uptime_str(started_epoch: Option<f64>, now_s: f64) -> String {
    let Some(started) = started_epoch.map(py_int) else {
        return String::new();
    };
    let delta = py_int(now_s) - started;
    if delta < 0 || started <= 0 {
        return String::new();
    }
    let (d, rem) = (delta / 86400, delta % 86400);
    let (h, rem) = (rem / 3600, rem % 3600);
    let (m, s) = (rem / 60, rem % 60);
    if d != 0 {
        format!("{d}d{h}h")
    } else if h != 0 {
        format!("{h}h{m}m")
    } else if m != 0 {
        format!("{m}m{s}s")
    } else {
        format!("{s}s")
    }
}

/// Local time parts the caller computed for an epoch (month, day, hour, minute, second).
pub type LocalParts = (u32, u32, u32, u32, u32);

/// `MM/DD HH:MM:SS` of the cabin start (cabin_since). The time conversion is the
/// caller's; without one, or for epoch <= 0, the answer is empty.
pub fn cabin_since(
    epoch: Option<f64>,
    localtime: Option<&dyn Fn(i64) -> Option<LocalParts>>,
) -> String {
    let Some(e) = epoch.map(py_int) else {
        return String::new();
    };
    let Some(lt) = localtime.filter(|_| e > 0) else {
        return String::new();
    };
    match lt(e) {
        Some((mo, d, h, mi, s)) => format!("{mo:02}/{d:02} {h:02}:{mi:02}:{s:02}"),
        None => String::new(),
    }
}

/// The optional parts of a title.
#[derive(Clone, Copy, Debug, Default)]
pub struct TitleParts<'a> {
    pub role: Option<&'a str>,
    pub color: bool,
    pub cabin: Option<&'a str>,
    pub since: Option<&'a str>,
    pub uptime: Option<&'a str>,
    pub version: Option<&'a str>,
    pub addr: Option<&'a str>,
    pub public: Option<&'a str>,
}

fn nonempty(s: Option<&str>) -> Option<&str> {
    s.filter(|v| !v.is_empty())
}

/// One title line (title_string):
/// `AIB [<public> | ][<addr>:][<cabin>] <beat> <light> <sig> <since [| uptime] | [role] code> [version]`.
pub fn title_string(sig: Option<&str>, l: &Light, beat_seq: i64, p: &TitleParts<'_>) -> String {
    let glyph = if p.color {
        format!("{}●\x1b[0m", l.level.ansi())
    } else {
        l.level.mono().to_string()
    };
    let mut parts: Vec<String> = vec!["AIB".into()];
    let cabin = nonempty(p.cabin);
    if let (Some(_), Some(public)) = (cabin, nonempty(p.public)) {
        parts.push(format!("{public} |"));
    }
    if let Some(c) = cabin {
        parts.push(match nonempty(p.addr) {
            Some(a) => format!("{a}:[{c}]"),
            None => format!("[{c}]"),
        });
    }
    parts.push(beat_glyph(beat_seq).into());
    parts.push(glyph);
    parts.push(nonempty(sig).unwrap_or("?").into());
    if let Some(since) = nonempty(p.since) {
        parts.push(since.into());
        if let Some(u) = nonempty(p.uptime) {
            parts.push(format!("| {u}"));
        }
    } else {
        if let Some(r) = nonempty(p.role) {
            parts.push(format!("[{r}]"));
        }
        parts.push(l.code.into());
    }
    if let Some(v) = nonempty(p.version) {
        parts.push(v.into());
    }
    parts.join(" ")
}

/// A model id to its short name, by rule (model_label): `claude-opus-5[1m]` → `Opus5`,
/// `claude-haiku-4-5-20251001` → `Haiku4.5`, `gpt-5.6-sol` → `Gpt5.6`. Empty when it
/// cannot tell (never a guess).
pub fn model_label(model_id: Option<&str>) -> String {
    let Some(m) = model_id else {
        return String::new();
    };
    let lower = py_strip(m).to_lowercase();
    let mut m = lower.split('[').next().unwrap_or("");
    for pref in ["us.anthropic.", "anthropic.", "claude-", "models/"] {
        if let Some(rest) = m.strip_prefix(pref) {
            m = rest;
        }
    }
    let replaced = m.replace('_', "-");
    let mut parts: Vec<&str> = replaced.split('-').filter(|t| !t.is_empty()).collect();
    if parts
        .last()
        .is_some_and(|t| t.chars().count() == 8 && py_isdigit(t))
    {
        parts.pop();
    }
    let Some(name) = parts.first() else {
        return String::new();
    };
    if !name.chars().next().is_some_and(char::is_alphabetic) {
        return String::new();
    }
    let nums: Vec<&str> = parts[1..]
        .iter()
        .copied()
        .take_while(|t| py_isdigit(&t.replace('.', "")))
        .collect();
    format!("{}{}", py_capitalize(name), nums.join("."))
}

/// The version without its pre-release tail: `0.42.1-alpha.1` → `0.42.1` (short_version).
pub fn short_version(version: Option<&str>) -> String {
    version
        .map(|v| py_strip(v).split('-').next().unwrap_or("").to_string())
        .unwrap_or_default()
}

/// `v<version>` with the update marks (version_segment). `None` for a source that could
/// not be read gives `?`; that is never the same as "nothing new".
pub fn version_segment(
    version: Option<&str>,
    behind: Option<bool>,
    code_changed: Option<bool>,
) -> String {
    if version.map(py_strip).is_none_or(str::is_empty) {
        return String::new();
    }
    let v = short_version(version);
    if v.is_empty() {
        return String::new();
    }
    let mut marks = String::new();
    if behind.is_none() || code_changed.is_none() {
        marks.push('?');
    }
    if behind == Some(true) {
        marks.push('↑');
    }
    if code_changed == Some(true) {
        marks.push('⟳');
    }
    format!("v{}{marks}", py_strip(&v))
}

/// The update state for the status line: `unknown` first, then `stale`, `behind`, `ok`
/// (version_state).
pub fn version_state(behind: Option<bool>, code_changed: Option<bool>) -> &'static str {
    match (behind, code_changed) {
        (None, _) | (_, None) => "unknown",
        (_, Some(true)) => "stale",
        (Some(true), _) => "behind",
        _ => "ok",
    }
}
