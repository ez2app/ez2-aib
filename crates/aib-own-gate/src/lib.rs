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

/// A `turn_start` line.
pub fn turn_start_line(seat: &str, now_ms: i64) -> Value {
    json!({"v": V, "at_ms": now_ms, "seat": seat, "kind": "turn_start",
           "turn_id": format!("{seat}-{now_ms}"), "source": "hook"})
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
/// 3. the turn has a done / progress / none line (not `manual`) ⇒ pass;
/// 4. no tool call this turn and it ended within [`REPLY_MS`] ⇒ pass with `exempt_reply`;
/// 5. the turn was held once already ⇒ pass with the second `blocked` line;
/// 6. otherwise hold, with the first `blocked` line naming `first` (the seat's first item).
///
/// `tools` is the number of tool calls this turn (`None` = the transcript could not be
/// read, taken as some, so a reply is never assumed).
pub fn decide(
    ledger: Result<&[Value], String>,
    seat: &str,
    tools: Option<usize>,
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
    if this.iter().any(|l| PROGRESS_KINDS.contains(&s(l, "kind"))) {
        return Verdict::Pass(None);
    }
    let started = this
        .iter()
        .find(|l| s(l, "kind") == "turn_start")
        .and_then(|l| l.get("at_ms").and_then(Value::as_i64))
        .unwrap_or(now_ms);
    if tools == Some(0) && now_ms - started < REPLY_MS {
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
        json!({"first": first, "n": held + 1}),
    );
    if held >= 1 {
        Verdict::Pass(Some(blocked))
    } else {
        Verdict::Hold(blocked)
    }
}

/// Tool calls in the current turn of a Claude Code transcript (JSONL): counted after the
/// last user entry that is a prompt (a string, or text parts without a tool result).
/// Nothing of what was said is kept; only `tool_use` parts are counted.
pub fn tools_this_turn(transcript: &str) -> usize {
    let mut n = 0;
    for l in transcript.lines() {
        let Ok(v) = serde_json::from_str::<Value>(l) else {
            continue;
        };
        let content = v.get("message").and_then(|m| m.get("content"));
        match s(&v, "type") {
            "user" => {
                let prompt = match content {
                    Some(Value::String(_)) => true,
                    Some(Value::Array(parts)) => {
                        !parts.iter().any(|p| s(p, "type") == "tool_result")
                    }
                    _ => false,
                };
                if prompt {
                    n = 0;
                }
            }
            "assistant" => {
                if let Some(Value::Array(parts)) = content {
                    n += parts.iter().filter(|p| s(p, "type") == "tool_use").count();
                }
            }
            _ => {}
        }
    }
    n
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
