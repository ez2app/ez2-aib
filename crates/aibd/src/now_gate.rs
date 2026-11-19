//! N-now slice 2 (519d0 05:24; 1.x `aib-now-gate.py stop`): a seat that did something this
//! turn may not hand the turn back without updating its status line. `aibd ctl now-gate` is the
//! seat's Stop hook (the settings file aibd writes when it opens the seat, b4297); it reads the
//! hook's JSON, finds where this turn began in the transcript, and asks aibd with the seat's own
//! token (act `seat_now_check`) whether the seat's line was written since then.
//!
//! - **Blocks** (exit 2, the reason on stderr): tools were used this turn and the line was not
//!   written since the turn began; or it was, but the stage is done (`reply` / `decide` /
//!   done = total) and the line lacks `track`, `why` or `idea` (1.x 11-12 review rule).
//! - **Lets go** (exit 0): `stop_hook_active` (Claude Code blocks a turn at most once); not a
//!   seat (no `AIB_SEAT_TOKEN`); a turn with no tools (just talk). And **fails open** — no
//!   transcript, no turn start, aibd not answering — since a gate that wedges every seat's Stop
//!   is worse than none (1.x rule).
//! - Reads the transcript from its end and stops at the turn's start, at most [`MAX_SCAN`] bytes.
//!
//! Slice 3 (1.x `aib-now-gate.py session-start`): the same command is the seat's SessionStart
//! hook. When the seat opens, or its conversation is cleared, compacted or resumed, it reads the
//! seat's own line back (act `seat_now_mine`) and prints it with the first thing to do, so a
//! seat that lost its context sees what it was doing ([`session_start`]). Prints nothing when
//! aibd does not answer (fails quiet, never blocks a start).

use serde_json::Value;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

/// The most of a transcript read back from its end before failing open.
pub const MAX_SCAN: u64 = 64 * 1024 * 1024;
const CHUNK: u64 = 64 * 1024;

/// What the transcript says about this turn.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Turn {
    /// The assistant used a tool this turn (sidechains do not count).
    pub tools: bool,
    /// The turn's first row's `timestamp` (UTC RFC 3339), `None` when not found.
    pub since: Option<String>,
}

/// A person (or a scheduled wake-up) handing the turn to the assistant (1.x `_is_turn_start`):
/// not a tool result, not a compact summary, not a sidechain, not a meta row unless scheduled,
/// not a task notification / hook feedback.
pub fn is_turn_start(row: &Value) -> bool {
    if row["type"] != "user" || row["isCompactSummary"] == true || row["isSidechain"] == true {
        return false;
    }
    if row["isMeta"] == true && row["turnOrigin"] != "scheduled" {
        return false;
    }
    if row["origin"]["kind"] == "task-notification" {
        return false;
    }
    match &row["message"]["content"] {
        Value::String(_) => true,
        Value::Array(blocks) => !blocks.iter().any(|b| b["type"] == "tool_result"),
        _ => false,
    }
}

fn used_a_tool(row: &Value) -> bool {
    row["type"] == "assistant"
        && row["isSidechain"] != true
        && row["message"]["content"]
            .as_array()
            .is_some_and(|c| c.iter().any(|b| b["type"] == "tool_use"))
}

/// Walk the transcript backwards to this turn's start.
pub fn turn(path: &Path) -> std::io::Result<Turn> {
    let mut f = std::fs::File::open(path)?;
    let mut pos = f.seek(SeekFrom::End(0))?;
    let floor = pos.saturating_sub(MAX_SCAN);
    let mut rest: Vec<u8> = Vec::new();
    let mut tools = false;
    while pos > floor {
        let n = CHUNK.min(pos - floor);
        pos -= n;
        f.seek(SeekFrom::Start(pos))?;
        let mut buf = vec![0u8; usize::try_from(n).unwrap_or(0)];
        f.read_exact(&mut buf)?;
        buf.extend_from_slice(&rest);
        let lines: Vec<&[u8]> = buf.split(|b| *b == b'\n').collect();
        // the first piece may be the tail of a line that starts in the next chunk back
        let (carry, lines) = if pos > floor {
            (lines[0].to_vec(), &lines[1..])
        } else {
            (Vec::new(), &lines[..])
        };
        rest = carry;
        for raw in lines.iter().rev() {
            let Ok(row) = serde_json::from_slice::<Value>(raw.trim_ascii()) else {
                continue;
            };
            if is_turn_start(&row) {
                let since = row["timestamp"].as_str().map(str::to_owned);
                return Ok(Turn { tools, since });
            }
            tools |= used_a_tool(&row);
        }
    }
    Ok(Turn { tools, since: None })
}

/// The reason to block, or `None` to let the turn end. `check` is aibd's answer to
/// `seat_now_check` (`Err` = aibd did not answer).
pub fn decide(hook: &Value, turn: Option<&Turn>, check: Result<&Value, &str>) -> Option<String> {
    if hook["stop_hook_active"] == true {
        return None;
    }
    let turn = turn?;
    if !turn.tools {
        return None;
    }
    turn.since.as_ref()?;
    let check = check.ok()?;
    if check["updated"] != true {
        return Some(
            "這一回合做了事,但狀態列沒更新:先用 seat_now 寫這一階段的主項目與第幾步 \
             (item、done、total;等別席 state=reply、等人決定 state=decide、全部做完 state=clear),再交回。"
                .to_owned(),
        );
    }
    if check["review_due"] == true {
        return Some(
            "這一階段做完了要交回:seat_now 同一行補上 track(0-100,跟主軸的符合度)、why(為什麼這麼做)、\
             idea(主人沒想到的建議),再交回。"
                .to_owned(),
        );
    }
    None
}

/// What a SessionStart hook prints into the seat's conversation: its own line back and the
/// first thing to do. `mine` is aibd's answer to `seat_now_mine` (`Err` = no answer ⇒ `None`).
pub fn session_start(hook: &Value, mine: Result<&Value, &str>) -> Option<String> {
    if hook["hook_event_name"] != "SessionStart" {
        return None;
    }
    let mine = mine.ok()?;
    const FIRST: &str = "第一個動作先用 seat_now 寫這一階段的主項目與第幾步(item、done、total;\
                         等別席 state=reply、等人決定 state=decide、全部做完 state=clear);\
                         之後每做完一個階段、交回之前再更新一次,沒更新回合結束會被擋。";
    let now = mine.get("now")?; // an answer without it is not from seat_now_mine
    if now.is_null() {
        return Some(format!("[AIB 狀態列] 這一席還沒有狀態列。{FIRST}"));
    }
    let state = match now["state"].as_str() {
        Some("reply") => "等回應",
        Some("decide") => "等決定",
        _ => "工作中",
    };
    let mut line = format!(
        "📌【{state}】{} | {}/{}",
        now["item"].as_str().unwrap_or(""),
        now["done"].as_u64().unwrap_or(0),
        now["total"].as_u64().unwrap_or(0),
    );
    if let Some(t) = now["track"].as_u64() {
        line.push_str(&format!(" | 主軸 {t}"));
    }
    if let Some(at) = now["updated_at"].as_str() {
        line.push_str(&format!(" | 更新 {at}"));
    }
    Some(format!(
        "[AIB 狀態列] 這一席上次寫的是:{line}\n接著做這一項,或換了主項目就改寫。{FIRST}"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn file(rows: &[Value]) -> tempfile::NamedTempFile {
        use std::io::Write;
        let mut f = tempfile::NamedTempFile::new().unwrap();
        for r in rows {
            writeln!(f, "{r}").unwrap();
        }
        f
    }

    fn person(at: &str) -> Value {
        json!({"type":"user","timestamp":at,"message":{"content":"做這件事"}})
    }
    fn tool_use() -> Value {
        json!({"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash"}]}})
    }
    fn tool_result() -> Value {
        json!({"type":"user","message":{"content":[{"type":"tool_result","content":"ok"}]}})
    }

    #[test]
    fn the_turn_starts_at_the_last_person_row_not_at_a_tool_result_or_a_notice() {
        let f = file(&[
            person("2026-11-18T05:00:00.000Z"),
            tool_use(),
            person("2026-11-18T06:00:00.000Z"),
            tool_use(),
            tool_result(),
            json!({"type":"user","isMeta":true,"message":{"content":"skill text"}}),
            json!({"type":"user","origin":{"kind":"task-notification"},"message":{"content":"done"}}),
            json!({"type":"user","isCompactSummary":true,"message":{"content":"summary"}}),
            json!({"type":"assistant","message":{"content":[{"type":"text","text":"好"}]}}),
        ]);
        let t = turn(f.path()).unwrap();
        assert_eq!(
            t.since.as_deref(),
            Some("2026-11-18T06:00:00.000Z"),
            "reason: wrong_turn_start"
        );
        assert!(t.tools);
        let talk = file(&[
            person("2026-11-18T06:00:00.000Z"),
            json!({"type":"assistant","message":{"content":[{"type":"text","text":"好"}]}}),
        ]);
        assert!(
            !turn(talk.path()).unwrap().tools,
            "reason: talk_counted_as_work"
        );
        // a scheduled wake-up is a new turn even though it is meta
        let woke = file(&[
            person("2026-11-18T05:00:00.000Z"),
            json!({"type":"user","isMeta":true,"turnOrigin":"scheduled","timestamp":"2026-11-18T07:00:00.000Z","message":{"content":"tick"}}),
            tool_use(),
        ]);
        assert_eq!(
            turn(woke.path()).unwrap().since.as_deref(),
            Some("2026-11-18T07:00:00.000Z")
        );
    }

    #[test]
    fn a_long_transcript_is_read_back_across_chunks() {
        // the person's row is longer than one chunk, and many rows follow it
        let said = "說".repeat(60_000);
        let pad = "x".repeat(1000);
        let mut rows = vec![
            json!({"type":"user","timestamp":"2026-11-18T06:00:00.000Z","message":{"content":said}}),
        ];
        for _ in 0..200 {
            rows.push(json!({"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"p":pad}}]}}));
        }
        let f = file(&rows);
        assert!(std::fs::metadata(f.path()).unwrap().len() > 3 * CHUNK);
        let t = turn(f.path()).unwrap();
        assert_eq!(
            t.since.as_deref(),
            Some("2026-11-18T06:00:00.000Z"),
            "reason: chunk_seam_lost_a_row"
        );
        assert!(t.tools);
    }

    #[test]
    fn work_without_an_update_is_blocked_and_everything_unsure_lets_go() {
        let work = Turn {
            tools: true,
            since: Some("2026-11-18T06:00:00.000Z".into()),
        };
        let hook = json!({"stop_hook_active": false});
        let stale = json!({"updated": false, "review_due": false});
        let fresh = json!({"updated": true, "review_due": false});
        let due = json!({"updated": true, "review_due": true});
        assert!(
            decide(&hook, Some(&work), Ok(&stale)).is_some(),
            "reason: stale_line_let_go"
        );
        assert!(
            decide(&hook, Some(&work), Ok(&due)).is_some(),
            "reason: stage_done_without_review"
        );
        assert!(decide(&hook, Some(&work), Ok(&fresh)).is_none());
        // Claude Code's once-per-turn flag, just talk, and every unknown: let go
        assert!(decide(&json!({"stop_hook_active": true}), Some(&work), Ok(&stale)).is_none());
        let talk = Turn {
            tools: false,
            ..work.clone()
        };
        assert!(
            decide(&hook, Some(&talk), Ok(&stale)).is_none(),
            "reason: talk_blocked"
        );
        let lost = Turn {
            since: None,
            ..work.clone()
        };
        assert!(decide(&hook, Some(&lost), Ok(&stale)).is_none());
        assert!(decide(&hook, None, Ok(&stale)).is_none());
        assert!(
            decide(&hook, Some(&work), Err("down")).is_none(),
            "reason: aibd_down_blocks"
        );
    }

    #[test]
    fn a_session_start_prints_the_seats_own_line_or_how_to_begin() {
        let start = json!({"hook_event_name":"SessionStart","source":"clear"});
        let line = json!({"now":{"item":"N-now 片3","done":1,"total":3,"state":"reply",
                                 "track":85,"updated_at":"2026-11-18T07:00:00.000Z"}});
        let out = session_start(&start, Ok(&line)).expect("reason: line_not_printed");
        assert!(
            out.contains("N-now 片3") && out.contains("1/3") && out.contains("等回應"),
            "{out}"
        );
        let none = session_start(&start, Ok(&json!({"now": null}))).expect("reason: no_first_step");
        assert!(
            none.contains("還沒有狀態列") && none.contains("seat_now"),
            "{none}"
        );
        // aibd not answering, or a Stop payload, prints nothing
        assert_eq!(
            session_start(&start, Err("down")),
            None,
            "reason: printed_without_answer"
        );
        assert_eq!(
            session_start(&start, Ok(&json!({"error": "unknown_act_type"}))),
            None,
            "reason: printed_a_foreign_answer"
        );
        assert_eq!(
            session_start(&json!({"hook_event_name":"Stop"}), Ok(&line)),
            None,
            "reason: printed_on_stop"
        );
    }
}
