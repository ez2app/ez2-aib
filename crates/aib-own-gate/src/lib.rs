//! S79-own o-3 (DECISION-council-s79-own-519d0-1116 r1; IFACE-s79-own-gate-a9136-1116 r1):
//! the hooks that make 「能做的先做、不要等」 something a program checks, not a rule an AI
//! has to remember.
//!
//! - `turn-start` (the prompt-submit hook) appends a `turn_start` line for the seat to
//!   `<root>/drive/own.jsonl`; it never holds anything.
//! - `stop` (the turn-end hook) reads that ledger: a turn with a `done` / `progress` / `none`
//!   line (aib-drive own, o-2) ends; a turn without one is held once with the seat's first
//!   item, and the second try ends with a `blocked` line, so a seat is never stuck.
//!
//! What this is not: a security line. The ledger is a file the same account can write; it
//! keeps the discipline visible (GUIDE-same-user-ai-threat-model). A fault — a ledger that
//! cannot be read or written, no seat, no turn start — always lets the turn end and says so.
use serde_json::{json, Value};

/// The ledger, under `<root>/drive`.
pub const LEDGER: &str = "own.jsonl";
/// Seats the gate holds, one five-hex code a line, `#` comments; every other seat passes
/// untouched (519d0 first for a day, Andy 11-16 08:3x).
pub const SEATS_FILE: &str = "own-gate-seats.txt";
/// Ledger line schema version.
pub const V: u64 = 1;
/// A turn that ends within this long with no tool call is a reply (r1 題 3: exempt_reply;
/// the human-present exemption is folded in, IFACE r1 §2).
pub const REPLY_MS: i64 = 120_000;

/// The round ledger (S79-own o5, `aib-drive round done`), under `<root>/drive`.
pub const ROUNDS: &str = "rounds.jsonl";
/// Who started a turn, as the round ledger writes it (S79 is 自動推進 since 11-16 §20).
pub const TRIGGER_AUTO: &str = "自動推進";
/// A turn a person typed.
pub const TRIGGER_HUMAN: &str = "真人";
/// The transcript does not say (an older CLI, another vendor): never held, but listed.
pub const TRIGGER_UNKNOWN: &str = "unknown";

/// Kinds o-2 writes that let a turn end.
pub const PROGRESS_KINDS: [&str; 3] = ["done", "progress", "none"];

/// What the stop hook does.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Verdict {
    /// The turn ends; the line, when there is one, is appended first.
    Pass(Option<Value>),
    /// The turn is held once; the line (`blocked`, n 1) is appended first.
    Hold(Value),
}

/// A five-hex seat code.
pub fn is_seat(s: &str) -> bool {
    s.len() == 5
        && s.bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
}

/// The seats the gate holds, read from [`SEATS_FILE`]'s text.
pub fn gated_seats(text: &str) -> Vec<String> {
    text.lines()
        .map(|l| l.split('#').next().unwrap_or("").trim())
        .filter(|l| is_seat(l))
        .map(str::to_owned)
        .collect()
}

/// Ledger lines from the file's text; a line that is not a JSON object is skipped (a torn
/// write must not make the whole ledger unreadable).
pub fn parse_ledger(text: &str) -> Vec<Value> {
    text.lines()
        .filter_map(|l| serde_json::from_str::<Value>(l).ok())
        .filter(Value::is_object)
        .collect()
}

fn s<'a>(v: &'a Value, k: &str) -> &'a str {
    v.get(k).and_then(Value::as_str).unwrap_or("")
}

/// The seat's current turn: its last `turn_start` line's id.
pub fn current_turn(lines: &[Value], seat: &str) -> Option<String> {
    lines
        .iter()
        .rev()
        .find(|l| s(l, "seat") == seat && s(l, "kind") == "turn_start")
        .map(|l| s(l, "turn_id").to_owned())
        .filter(|t| !t.is_empty())
}

/// When the seat's current turn began: its last `turn_start` line's `at_ms`.
pub fn current_turn_start_ms(lines: &[Value], seat: &str) -> Option<i64> {
    lines
        .iter()
        .rev()
        .find(|l| s(l, "seat") == seat && s(l, "kind") == "turn_start")
        .and_then(|l| l.get("at_ms").and_then(Value::as_i64))
}

/// A `turn_start` line; `transcript` (the hook's `transcript_path`) lets `aib-drive round
/// done` tell later who started the turn.
pub fn turn_start_line(seat: &str, now_ms: i64, transcript: Option<&str>) -> Value {
    let mut v = json!({"v": V, "at_ms": now_ms, "seat": seat, "kind": "turn_start",
           "turn_id": format!("{seat}-{now_ms}"), "source": "hook"});
    if let (Some(m), Some(t)) = (v.as_object_mut(), transcript.filter(|t| !t.is_empty())) {
        m.insert("transcript".into(), Value::from(t));
    }
    v
}

/// The current turn's `transcript` path from its `turn_start` line, if the hook gave one.
pub fn current_turn_transcript(lines: &[Value], seat: &str) -> Option<String> {
    lines
        .iter()
        .rev()
        .find(|l| s(l, "seat") == seat && s(l, "kind") == "turn_start")
        .map(|l| s(l, "transcript").to_owned())
        .filter(|t| !t.is_empty())
}

/// Who started the current turn, read from a Claude Code transcript's text (or its tail):
/// the last prompt entry's `turnOrigin` — `scheduled` (a timed prompt) is [`TRIGGER_AUTO`],
/// `human` is [`TRIGGER_HUMAN`]; `task_notification` follows the prompt before it. A prompt
/// without the field (a slash command, an older CLI) is [`TRIGGER_UNKNOWN`]. Tool results,
/// meta lines, a compact summary and system lines without the field are not prompts. Nothing that was said is kept. A bookkeeping split, not a security line:
/// the same account can write the file.
pub fn trigger_of(transcript: &str) -> &'static str {
    for l in transcript.lines().rev() {
        let Ok(v) = serde_json::from_str::<Value>(l) else {
            continue;
        };
        if s(&v, "type") != "user" || v.get("toolUseResult").is_some() {
            continue;
        }
        let tool_result = matches!(
            v.get("message").and_then(|m| m.get("content")),
            Some(Value::Array(parts)) if parts.iter().any(|p| s(p, "type") == "tool_result")
        );
        if tool_result {
            continue;
        }
        match v.get("turnOrigin").and_then(Value::as_str) {
            Some("scheduled") => return TRIGGER_AUTO,
            Some("human") => return TRIGGER_HUMAN,
            Some("task_notification") => continue,
            Some(_) => return TRIGGER_UNKNOWN,
            // meta lines, a compact summary, and system lines from before the field
            // (task notifications) are not prompts
            None if v.get("isMeta").and_then(Value::as_bool) == Some(true)
                || v.get("isCompactSummary").and_then(Value::as_bool) == Some(true)
                || s(&v, "promptSource") == "system" =>
            {
                continue
            }
            None => return TRIGGER_UNKNOWN,
        }
    }
    TRIGGER_UNKNOWN
}

/// Whether the seat has a `round` line at or after `start_ms` in the round ledger's lines.
/// Both shapes count: aib-drive writes the aib-ledger envelope v 2 (`ev`, `occurred_at`; IFACE
/// -ledger-envelope-b433b-1117 r3), the v 1 lines before it have `kind` and `at_ms`.
pub fn round_logged(rounds: &[Value], seat: &str, start_ms: i64) -> bool {
    rounds.iter().any(|l| {
        s(l, "seat") == seat
            && (s(l, "ev") == "round" || s(l, "kind") == "round")
            && l.get("occurred_at")
                .and_then(Value::as_str)
                .and_then(aib_ledger::envelope::parse_rfc3339_ms)
                .or_else(|| l.get("at_ms").and_then(Value::as_i64))
                .is_some_and(|t| t >= start_ms)
    })
}

/// The round check of a turn (S79-own o5): only a turn 自動推進 started needs a round line.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Round {
    /// Not a 自動推進 turn (a person, unknown, or the round ledger could not be read).
    NotNeeded,
    /// A 自動推進 turn with its round line.
    Logged,
    /// A 自動推進 turn without one.
    Missing,
}

fn line(seat: &str, turn: &str, kind: &str, now_ms: i64, extra: Value) -> Value {
    let mut v = json!({"v": V, "at_ms": now_ms, "seat": seat, "kind": kind, "turn_id": turn});
    if let (Some(m), Value::Object(e)) = (v.as_object_mut(), extra) {
        m.extend(e);
    }
    v
}

/// What the stop hook decides (IFACE r1 §2, in this order; the first that holds wins):
/// 1. the ledger could not be read ⇒ pass with `fault`;
/// 2. no turn start for the seat ⇒ pass with `unreadable`;
/// 3. the turn has a done / progress / none line (not `manual`), and `round` is not
///    [`Round::Missing`] ⇒ pass;
/// 4. no tool call this turn and it ended within [`REPLY_MS`], `round` not missing ⇒ pass
///    with `exempt_reply` (a 自動推進 round is written down even when nothing was done);
/// 5. the turn was held once already ⇒ pass with the second `blocked` line;
/// 6. otherwise hold, with the first `blocked` line naming `first` (the seat's first item).
///
/// `tools` is the number of tool calls this turn (`None` = the transcript could not be
/// read, taken as some, so a reply is never assumed). The `blocked` line's `missing` names
/// what the turn lacked: `own`, `round`, or both.
pub fn decide(
    ledger: Result<&[Value], String>,
    seat: &str,
    tools: Option<usize>,
    round: Round,
    first: &str,
    now_ms: i64,
) -> Verdict {
    let lines = match ledger {
        Ok(l) => l,
        Err(e) => {
            return Verdict::Pass(Some(line(
                seat,
                "",
                "fault",
                now_ms,
                json!({"what": LEDGER, "err": e}),
            )))
        }
    };
    let Some(turn) = current_turn(lines, seat) else {
        return Verdict::Pass(Some(line(
            seat,
            "",
            "unreadable",
            now_ms,
            json!({"what": "turn_start", "err": "no turn_start for this seat"}),
        )));
    };
    let this: Vec<&Value> = lines
        .iter()
        .filter(|l| s(l, "seat") == seat && s(l, "turn_id") == turn)
        .collect();
    let own_ok = this.iter().any(|l| PROGRESS_KINDS.contains(&s(l, "kind")));
    let round_ok = round != Round::Missing;
    if own_ok && round_ok {
        return Verdict::Pass(None);
    }
    let started = this
        .iter()
        .find(|l| s(l, "kind") == "turn_start")
        .and_then(|l| l.get("at_ms").and_then(Value::as_i64))
        .unwrap_or(now_ms);
    if round_ok && tools == Some(0) && now_ms - started < REPLY_MS {
        return Verdict::Pass(Some(line(
            seat,
            &turn,
            "exempt_reply",
            now_ms,
            json!({"why": "no tool call, under two minutes"}),
        )));
    }
    let held = this.iter().filter(|l| s(l, "kind") == "blocked").count();
    let blocked = line(
        seat,
        &turn,
        "blocked",
        now_ms,
        json!({"first": first, "n": held + 1, "missing": missing(own_ok, round_ok)}),
    );
    if held >= 1 {
        Verdict::Pass(Some(blocked))
    } else {
        Verdict::Hold(blocked)
    }
}

fn missing(own_ok: bool, round_ok: bool) -> Vec<&'static str> {
    let mut m = Vec::new();
    if !own_ok {
        m.push("own");
    }
    if !round_ok {
        m.push("round");
    }
    m
}

/// How much of the transcript's end the stop hook reads (a seat's transcript runs to tens of
/// megabytes; a turn's tool calls are at its end).
pub const TAIL_BYTES: u64 = 4 * 1024 * 1024;

/// Milliseconds since the epoch of an RFC 3339 UTC time as Claude Code writes it
/// (`2026-11-16T01:13:32.123Z`); `None` for anything else.
pub fn iso_ms(t: &str) -> Option<i64> {
    let b = t.as_bytes();
    if b.len() < 20
        || b[4] != b'-'
        || b[7] != b'-'
        || b[10] != b'T'
        || b[13] != b':'
        || b[16] != b':'
    {
        return None;
    }
    if !t.ends_with('Z') {
        return None;
    }
    let num = |a: usize, z: usize| {
        t.get(a..z)
            .filter(|x| x.bytes().all(|c| c.is_ascii_digit()))
            .and_then(|x| x.parse::<i64>().ok())
    };
    let (y, mo, d) = (num(0, 4)?, num(5, 7)?, num(8, 10)?);
    let (h, mi, se) = (num(11, 13)?, num(14, 16)?, num(17, 19)?);
    if !(1..=12).contains(&mo) || !(1..=31).contains(&d) || h > 23 || mi > 59 || se > 60 {
        return None;
    }
    let frac = &t[19..t.len() - 1];
    let ms = if frac.is_empty() {
        0
    } else {
        let f = frac.strip_prefix('.')?;
        if f.is_empty() || !f.bytes().all(|c| c.is_ascii_digit()) {
            return None;
        }
        let f3: String = f.chars().chain("000".chars()).take(3).collect();
        f3.parse::<i64>().ok()?
    };
    // days from civil (Howard Hinnant)
    let yy = if mo <= 2 { y - 1 } else { y };
    let era = yy.div_euclid(400);
    let yoe = yy - era * 400;
    let mp = (mo + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    Some(((days * 24 + h) * 60 + mi) * 60_000 + se * 1000 + ms)
}

/// Tool calls in this turn, read from a Claude Code transcript's text (JSONL, or its tail):
/// `tool_use` parts of assistant entries stamped at or after `start_ms` (the turn_start
/// line). Messages that arrive mid-turn do not reset the count. Nothing that was said is
/// kept. `complete` = the text is the whole file. `None` = cannot tell (the tail starts
/// after the turn began and shows no call), which the gate takes as "some".
pub fn tools_since(transcript: &str, start_ms: i64, complete: bool) -> Option<usize> {
    let mut n = 0;
    let mut earliest: Option<i64> = None;
    for l in transcript.lines() {
        let Ok(v) = serde_json::from_str::<Value>(l) else {
            continue;
        };
        let Some(at) = v.get("timestamp").and_then(Value::as_str).and_then(iso_ms) else {
            continue;
        };
        earliest = Some(earliest.map_or(at, |e: i64| e.min(at)));
        if at < start_ms || s(&v, "type") != "assistant" {
            continue;
        }
        if let Some(Value::Array(parts)) = v.get("message").and_then(|m| m.get("content")) {
            n += parts.iter().filter(|p| s(p, "type") == "tool_use").count();
        }
    }
    if n > 0 || complete || earliest.is_some_and(|e| e <= start_ms) {
        Some(n)
    } else {
        None
    }
}

/// The seat a hook's session belongs to: the session id's last five characters, but only
/// when a seat file names that session (`session_uuid`); otherwise none (never guessed).
pub fn seat_of(session_id: &str, seat_files: &[Value]) -> Option<String> {
    let id = session_id.trim();
    if id.len() < 5 || !seat_files.iter().any(|f| s(f, "session_uuid") == id) {
        return None;
    }
    let tail = &id[id.len() - 5..];
    is_seat(tail).then(|| tail.to_owned())
}
