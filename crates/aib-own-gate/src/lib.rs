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

/// The state root: `env_root` (EZ2_AIB_STATE_ROOT), else `aib_home` (EZ2_AIB_HOME), else
/// `<home>/.ez2-aib`; an empty variable counts as not set. The same order as aib-drive's
/// `observe::state_root`, so the gate and the writer of the round ledger agree (1ced7 o5 L2).
pub fn state_root(
    env_root: Option<&str>,
    aib_home: Option<&str>,
    home: Option<&std::path::Path>,
) -> Option<std::path::PathBuf> {
    if let Some(r) = env_root.filter(|r| !r.is_empty()) {
        return Some(r.into());
    }
    if let Some(h) = aib_home.filter(|h| !h.is_empty()) {
        return Some(h.into());
    }
    home.map(|h| h.join(".ez2-aib"))
}
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

/// The card check of a turn (S107-loop; DECISION-council-board-coop-519d0-1117 §2.5 rule 2, Andy
/// 11-17 06:3x): a turn that used a shared resource — ran a receipt, pushed, merged, took a test
/// machine — ends only after the seat wrote a card (`aib-plan set / note / add`, one line in
/// [`PLAN_EVENTS`]). A turn that only read, reviewed or answered is never held for this.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Card {
    /// No resource used this turn (or the card ledger could not be read: never holds).
    NotNeeded,
    /// A resource was used and the seat wrote a card since the turn began.
    Written,
    /// A resource was used and no card was written.
    Missing,
}

/// The card event ledger `aib-plan set / note / add` appends to, under the state root.
pub const PLAN_EVENTS: &str = "pending/aib2-a63ab-1107/cabin-plan/plan-events.jsonl";

/// Receipt scripts: they count only when run (`bash <path>` / `sh <path>` / `./<path>`), not
/// when a command reads, copies or greps them, and not for their bookkeeping modes
/// ([`RECEIPT_NOT_RUN`]) (5d33a 4ab7b2ea N2).
pub const RECEIPT_SCRIPTS: [&str; 3] = ["receipt-remote.sh", "receipt-run.sh", "cell-steps"];
/// A receipt script's modes that run nothing on a test machine.
pub const RECEIPT_NOT_RUN: [&str; 3] = ["--fetch", "--withdraw", "--clean"];

/// Other commands that use a shared resource, and what each is called on the held message.
/// A bookkeeping split, not a security line: a command can be written around it.
pub const RESOURCE_MARKS: [(&str, &str); 3] = [
    ("git push", "推送"),
    ("gh pr merge", "合併"),
    ("aib lease acquire", "測試機"),
];

/// Whether `cmd` runs one of [`RECEIPT_SCRIPTS`]: a word naming it that ends in `.sh` and is
/// either started by `bash` / `sh` or is itself the command word (after any `VAR=value`), and
/// that run has none of [`RECEIPT_NOT_RUN`] before the next `;` `&` `|` or line end. A path
/// handed to another program (`cat`, `grep`, `cp`) is not a run.
pub fn runs_receipt(cmd: &str) -> bool {
    cmd.split([';', '&', '|', '\n']).any(|part| {
        let w: Vec<&str> = part.split_whitespace().map(unquote).collect();
        // the command word: the first one that is not a VAR=value assignment
        let cmd_at = w
            .iter()
            .position(|t| !t.split('/').next().unwrap_or("").contains('='));
        w.iter().enumerate().any(|(i, t)| {
            let names = RECEIPT_SCRIPTS.iter().any(|m| t.contains(m)) && t.ends_with(".sh");
            let started = shell_runs(&w, i) || cmd_at == Some(i);
            names && started && !w[i + 1..].iter().any(|a| RECEIPT_NOT_RUN.contains(a))
        })
    })
}

/// A shell word with its surrounding quotes taken off (`"$T/receipt-run.sh"` → `$T/receipt-run.sh`,
/// 5d33a 82feaf6d L1).
fn unquote(t: &str) -> &str {
    t.trim_matches(|c| c == '"' || c == '\'')
}

/// `w[i]` is the script a shell runs: walk back over the shell's flags (`-x`; `-eo pipefail`, where a
/// flag ending in `o` takes the next word as an option name) to a word whose file name is bash / sh
/// (5d33a 82feaf6d L1).
fn shell_runs(w: &[&str], i: usize) -> bool {
    let flag = |t: &str| t.starts_with('-') || t.starts_with('+');
    let mut k = i;
    while k > 0 {
        if flag(w[k - 1]) {
            k -= 1;
        } else if k >= 2 && flag(w[k - 2]) && w[k - 2].ends_with('o') {
            k -= 2;
        } else {
            break;
        }
    }
    k > 0 && matches!(w[k - 1].rsplit('/').next(), Some("bash" | "sh"))
}

/// The resources this turn's shell commands used, in first-seen order, from a Claude Code
/// transcript's text (or its tail): `Bash` tool calls of assistant entries stamped at or after
/// `start_ms`. Fetching a receipt back (`--fetch`) is not running one.
pub fn resources_since(transcript: &str, start_ms: i64) -> Vec<&'static str> {
    let mut out: Vec<&'static str> = Vec::new();
    for l in transcript.lines() {
        let Ok(v) = serde_json::from_str::<Value>(l) else {
            continue;
        };
        let at = v.get("timestamp").and_then(Value::as_str).and_then(iso_ms);
        if !at.is_some_and(|a| a >= start_ms) || s(&v, "type") != "assistant" {
            continue;
        }
        let Some(Value::Array(parts)) = v.get("message").and_then(|m| m.get("content")) else {
            continue;
        };
        for p in parts {
            if s(p, "type") != "tool_use" || s(p, "name") != "Bash" {
                continue;
            }
            let cmd = p.get("input").map_or("", |i| s(i, "command"));
            let mut hit: Vec<&'static str> = Vec::new();
            if runs_receipt(cmd) {
                hit.push("收據");
            }
            hit.extend(
                RESOURCE_MARKS
                    .iter()
                    .filter(|(m, _)| cmd.contains(m))
                    .map(|(_, w)| *w),
            );
            for what in hit {
                if !out.contains(&what) {
                    out.push(what);
                }
            }
        }
    }
    out
}

/// Whether the seat wrote a card since `start_ms`: a [`PLAN_EVENTS`] line with `by` = the seat
/// and `ts` (whole seconds) in or after the second the turn started — `ts` is rounded down, so
/// a card written in that same second counts (5d33a 4ab7b2ea N1). A torn line is skipped.
pub fn card_written(events: &str, seat: &str, start_ms: i64) -> bool {
    parse_ledger(events).iter().any(|e| {
        s(e, "by") == seat
            && e.get("ts")
                .and_then(Value::as_i64)
                .is_some_and(|t| t.saturating_add(1).saturating_mul(1000) > start_ms)
    })
}

/// What turn-start puts in front of the seat (S107-loop 開工自動塞卡): from `aib-plan mine
/// --json`, the first card the seat can push (rows come rightmost station first), or that there
/// is none and the seat stops. `None` = the output is not mine's shape (an older aib-plan): say
/// nothing rather than guess.
/// How much of a card's `next` turn-start shows (5d33a 82feaf6d N1).
pub const NEXT_MAX: usize = 120;

/// Plan-tree text put in front of a seat: control characters dropped, cut at [`NEXT_MAX`]
/// characters with `…` (the tree is written by every seat; 5d33a 82feaf6d N1).
fn tree_text(t: &str) -> String {
    let clean: String = t.chars().filter(|c| !c.is_control()).collect();
    match clean.char_indices().nth(NEXT_MAX) {
        Some((at, _)) => format!("{}…", &clean[..at]),
        None => clean,
    }
}

pub fn card_feed(mine_json: &str) -> Option<String> {
    let v: Value = serde_json::from_str(mine_json.trim()).ok()?;
    let rows = v.get("rows")?.as_array()?;
    let first = rows
        .iter()
        .find(|r| r.get("pushable").and_then(Value::as_bool) == Some(true));
    match first {
        Some(r) => {
            let head = r
                .get("head")
                .and_then(Value::as_str)
                .map(|h| format!(" 頭 {}", h.get(..8).unwrap_or(h)))
                .unwrap_or_default();
            Some(format!(
                "開工(S107-loop):先推 {}({}・{}{})— 進度樹原文:「{}」。換站 / 補事實:aib-plan set / note",
                tree_text(s(r, "id")),
                tree_text(s(r, "station")),
                tree_text(s(r, "role")),
                head,
                tree_text(s(r, "next"))
            ))
        }
        None if v.get("stop").and_then(Value::as_bool) == Some(true) => {
            Some("開工(S107-loop):名下沒有推得動的卡 ⇒ 停(aib-plan mine 看全部)".to_owned())
        }
        None => None,
    }
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
    decide_with_card(ledger, seat, tools, round, Card::NotNeeded, first, now_ms)
}

/// [`decide`] with the card check (S107-loop): [`Card::Missing`] holds the turn once like a
/// missing own line does, and the `blocked` line's `missing` lists `card`.
pub fn decide_with_card(
    ledger: Result<&[Value], String>,
    seat: &str,
    tools: Option<usize>,
    round: Round,
    card: Card,
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
    let card_ok = card != Card::Missing;
    if own_ok && round_ok && card_ok {
        return Verdict::Pass(None);
    }
    let started = this
        .iter()
        .find(|l| s(l, "kind") == "turn_start")
        .and_then(|l| l.get("at_ms").and_then(Value::as_i64))
        .unwrap_or(now_ms);
    if round_ok && card_ok && tools == Some(0) && now_ms - started < REPLY_MS {
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
        json!({"first": first, "n": held + 1, "missing": missing(own_ok, round_ok, card_ok)}),
    );
    if held >= 1 {
        Verdict::Pass(Some(blocked))
    } else {
        Verdict::Hold(blocked)
    }
}

fn missing(own_ok: bool, round_ok: bool, card_ok: bool) -> Vec<&'static str> {
    let mut m = Vec::new();
    if !card_ok {
        m.push("card");
    }
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
