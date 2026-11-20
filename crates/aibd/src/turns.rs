//! N-workspace (b4297, ORDER-workspace-519d0-1118): whether a seat is in a turn.
//!
//! A turn starts when a text is written to the seat's input and ends when its provider prints
//! the line that ends a turn (`{"type":"result",…}` on stdout, the same line `aibd ctl chat`
//! stops at). A message one seat calls another with (`@`) is typed only between turns: sent in
//! the middle of one, it waits as a queued row and is written when that turn ends
//! (seat_send::redeliver), so a seat is not handed a second task half-way through the first.
//! A person's own typing is not held back: the provider keeps it for after the turn itself.
//!
//! Memory only: after a restart no seat is in a turn (its session is new, its first text
//! starts one).

//!
//! N-webonly-c44 (7ebca 11-20; 0eed7 at 47788: a codex seat took a text and its model said
//! nothing for 3 minutes, the page could only show "thinking"): a turn also keeps when it began
//! and when its model last put anything out ([`Turn`]), so a page can tell "the text arrived and
//! nothing came back" from "it is working". Times only, never a word of the output.

use std::collections::HashMap;
use std::sync::Mutex;

/// One seat's turn under way: when its text was written, and when its model last put anything
/// out since (`None`: nothing yet). UTC RFC 3339, as [`crate::sessions::now_rfc3339`] writes.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Turn {
    pub started_at: String,
    pub output_at: Option<String>,
}

/// The seats that are in a turn now.
#[derive(Default)]
pub struct Turns {
    seats: Mutex<HashMap<String, Turn>>,
}

impl Turns {
    fn seats(&self) -> std::sync::MutexGuard<'_, HashMap<String, Turn>> {
        self.seats.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// A text was written to `seat`: its turn runs until the provider ends it.
    pub fn began(&self, seat: &str) {
        self.began_at(seat, &crate::sessions::now_rfc3339());
    }

    /// [`Turns::began`] at a given time. A text written into a turn already under way (a
    /// person's own typing) does not start it again: the first text's time stays.
    pub fn began_at(&self, seat: &str, at: &str) {
        let mut g = self.seats();
        if !g.contains_key(seat) {
            let turn = Turn {
                started_at: at.to_owned(),
                output_at: None,
            };
            g.insert(seat.to_owned(), turn);
        }
    }

    /// `seat`'s provider ended a turn (or its session started anew); whether it was in one.
    pub fn ended(&self, seat: &str) -> bool {
        self.seats().remove(seat).is_some()
    }

    pub fn in_turn(&self, seat: &str) -> bool {
        self.seats().contains_key(seat)
    }

    /// `seat`'s model put something out at `at`. Outside a turn it is not kept.
    pub fn output(&self, seat: &str, at: &str) {
        if let Some(t) = self.seats().get_mut(seat) {
            t.output_at = Some(at.to_owned());
        }
    }

    /// `seat`'s turn under way, if it is in one.
    pub fn turn_of(&self, seat: &str) -> Option<Turn> {
        self.seats().get(seat).cloned()
    }
}

/// How much of a cut line is looked at for what it is.
const HEAD: usize = 256;

fn head_has(line: &[u8], needle: &[u8]) -> bool {
    let head = line.get(..HEAD).unwrap_or(line);
    head.windows(needle.len()).any(|w| w == needle)
}

/// Whether one stdout line of a Claude seat (stream-json) is the model at work. Claude prints
/// `{"type":"system","subtype":"init"}` at the start of every turn, about 0.1 s after the text
/// and before the model is heard from, and `rate_limit_event` lines of its own (measured on
/// this machine's aibd events, 49 turns): those are not output. What counts is named, so a new
/// kind of line of Claude's own does not read as an answer: `assistant`, `stream_event`,
/// `result`, a `user` line that is not a replay (a tool's result), and the `system` lines
/// `thinking_tokens` / `permission_denied`. A line cut at the reader's cap is read by its start.
pub fn claude_speaks(line: &[u8], cut: bool) -> bool {
    if cut {
        return [
            &b"{\"type\":\"assistant\""[..],
            b"{\"type\":\"stream_event\"",
            b"{\"type\":\"result\"",
            b"{\"type\":\"user\"",
        ]
        .iter()
        .any(|p| line.starts_with(p));
    }
    let Ok(v) = serde_json::from_slice::<serde_json::Value>(line) else {
        return false;
    };
    let kind = v.get("type").and_then(|t| t.as_str()).unwrap_or_default();
    let sub = v
        .get("subtype")
        .and_then(|t| t.as_str())
        .unwrap_or_default();
    match kind {
        "assistant" | "stream_event" | "result" => true,
        "user" => v.get("isReplay").and_then(|r| r.as_bool()) != Some(true),
        "system" => matches!(sub, "thinking_tokens" | "permission_denied"),
        _ => false,
    }
}

/// Whether one stdout line of `codex app-server` is the model's own output: an `item/…`
/// notification that is not the echo of what the seat was just told (`userMessage`,
/// `hookPrompt`). codex answers a `turn/start` by itself (its result, `turn/started`, the echo
/// of the text, status and rate-limit notes) before the model says anything, so "any line"
/// would read a model that never answers as one that did (method and item names: codex-cli
/// 0.167.0 `app-server generate-json-schema`, ServerNotification / ThreadItem). A line cut at
/// the reader's cap cannot be parsed: its start is read the same way.
pub fn codex_speaks(line: &[u8], cut: bool) -> bool {
    if cut {
        let item = head_has(line, b"\"method\":\"item/");
        let told = head_has(line, b"\"userMessage\"") || head_has(line, b"\"hookPrompt\"");
        return item && !told;
    }
    let Ok(v) = serde_json::from_slice::<serde_json::Value>(line) else {
        return false;
    };
    let is_item = v
        .get("method")
        .and_then(|m| m.as_str())
        .is_some_and(|m| m.starts_with("item/"));
    let told = matches!(
        v.pointer("/params/item/type").and_then(|t| t.as_str()),
        Some("userMessage" | "hookPrompt")
    );
    is_item && !told
}

/// The start of a result line as providers print it (compact JSON, `type` first).
const RESULT_START: &[u8] = b"{\"type\":\"result\"";

/// Whether one stdout line of a provider ends its turn: a JSON object whose `type` is
/// `result`. A line cut at the reader's cap (`cut`; a long final answer) cannot be parsed, so
/// it ends the turn when it starts the way a result line does. Anything else does not.
pub fn ends_turn(line: &[u8], cut: bool) -> bool {
    if cut {
        return line.starts_with(RESULT_START);
    }
    // most lines are not results: only those that could be are parsed
    if !line.windows(8).any(|w| w == b"\"result\"") {
        return false;
    }
    serde_json::from_slice::<serde_json::Value>(line)
        .ok()
        .and_then(|v| {
            v.get("type")
                .and_then(|t| t.as_str())
                .map(|t| t == "result")
        })
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ends_turn_whole(line: &[u8]) -> bool {
        ends_turn(line, false)
    }

    #[test]
    fn only_a_result_line_ends_a_turn() {
        assert!(ends_turn_whole(br#"{"type":"result","result":"done"}"#));
        assert!(ends_turn_whole(
            br#"{"type":"result","subtype":"success","is_error":false}"#
        ));
        // Windows providers end lines with \r\n; the reader drops it, a stray \r still parses
        assert!(ends_turn_whole(b"{\"type\":\"result\"}\r"));
        assert!(!ends_turn_whole(
            br#"{"type":"assistant","message":{"content":"result"}}"#
        ));
        assert!(!ends_turn_whole(br#"{"type":"user","result":"x"}"#));
        assert!(!ends_turn_whole(br#"{"type":"result""#));
        assert!(!ends_turn_whole(b"the \"result\" is in"));
        assert!(!ends_turn_whole(b""));
        // a long final answer is cut by the reader: its start still says it is a result
        assert!(ends_turn(br#"{"type":"result","result":"aaaa"#, true));
        assert!(!ends_turn(br#"{"type":"assistant","message":"#, true));
    }

    #[test]
    fn a_turn_runs_from_a_write_to_its_result() {
        let t = Turns::default();
        assert!(!t.in_turn("a"));
        t.began("a");
        assert!(t.in_turn("a") && !t.in_turn("b"));
        assert!(t.ended("a"), "reason: turn_not_ended");
        assert!(!t.in_turn("a"));
        assert!(!t.ended("a"), "reason: ended_twice");
    }

    /// c44: a turn keeps when it began and when its model last put anything out.
    #[test]
    fn a_turn_keeps_when_it_began_and_its_last_output() {
        let t = Turns::default();
        assert_eq!(t.turn_of("a"), None);
        // output outside a turn (the lines of the turn before) is not this turn's
        t.output("a", "2026-11-20T10:00:00.000Z");
        t.began_at("a", "2026-11-20T10:00:01.000Z");
        let now = t.turn_of("a").unwrap();
        assert_eq!(now.started_at, "2026-11-20T10:00:01.000Z");
        assert_eq!(now.output_at, None, "reason: old_output_counted");
        t.output("a", "2026-11-20T10:00:05.000Z");
        t.output("b", "2026-11-20T10:00:06.000Z");
        assert_eq!(
            t.turn_of("a").unwrap().output_at.as_deref(),
            Some("2026-11-20T10:00:05.000Z"),
            "reason: output_not_kept"
        );
        assert_eq!(t.turn_of("b"), None);
        // a text typed into the turn under way does not start it again
        t.began_at("a", "2026-11-20T10:00:09.000Z");
        let now = t.turn_of("a").unwrap();
        assert_eq!(
            now.started_at, "2026-11-20T10:00:01.000Z",
            "reason: turn_restarted"
        );
        assert!(now.output_at.is_some(), "reason: output_dropped");
        // the next turn starts clean
        assert!(t.ended("a"));
        t.began_at("a", "2026-11-20T10:01:00.000Z");
        assert_eq!(t.turn_of("a").unwrap().output_at, None);
    }

    /// c44: what Claude prints by itself at the start of a turn is not the model's output.
    /// The lines are the shapes this machine's aibd recorded (keys only, no content).
    #[test]
    fn only_the_models_own_lines_are_claude_output() {
        let says = |l: &[u8]| claude_speaks(l, false);
        // every turn starts with these, model or no model
        assert!(
            !says(br#"{"type":"system","subtype":"init","session_id":"s","model":"m","tools":[]}"#),
            "reason: init_is_output"
        );
        assert!(!says(
            br#"{"type":"rate_limit_event","rate_limit_info":{},"uuid":"u","session_id":"s"}"#
        ));
        assert!(!says(
            br#"{"type":"user","isReplay":true,"message":{"role":"user","content":[]}}"#
        ));
        assert!(!says(br#"{"type":"system","subtype":"hook_started"}"#));
        assert!(!says(b"not json"));
        // the model at work
        assert!(says(br#"{"type":"system","subtype":"thinking_tokens"}"#));
        assert!(says(
            br#"{"type":"assistant","message":{"role":"assistant","content":[{"type":"thinking"}]}}"#
        ));
        assert!(says(
            br#"{"type":"user","message":{"role":"user","content":[{"type":"tool_result"}]}}"#
        ));
        assert!(says(br#"{"type":"system","subtype":"permission_denied"}"#));
        assert!(says(br#"{"type":"stream_event","event":{}}"#));
        assert!(says(br#"{"type":"result","subtype":"success"}"#));
        // cut at the reader's cap: read by its start
        assert!(claude_speaks(
            br#"{"type":"assistant","message":{"content":[{"text":"aaa"#,
            true
        ));
        assert!(
            !claude_speaks(br#"{"type":"system","subtype":"init","tools":["aaa"#, true),
            "reason: cut_init_is_output"
        );
    }

    /// c44: what codex prints by itself after a `turn/start` is not the model's output.
    #[test]
    fn only_the_models_own_items_are_codex_output() {
        let says = |l: &[u8]| codex_speaks(l, false);
        // what codex prints before the model says anything
        assert!(!says(br#"{"id":3,"result":{"turn":{"id":"t1"}}}"#));
        assert!(!says(
            br#"{"method":"turn/started","params":{"turn":{"id":"t1"}}}"#
        ));
        assert!(!says(
            br#"{"method":"item/started","params":{"item":{"type":"userMessage","id":"i1"}}}"#
        ));
        assert!(!says(
            br#"{"method":"item/completed","params":{"item":{"type":"userMessage","id":"i1"}}}"#
        ));
        assert!(!says(
            br#"{"method":"item/started","params":{"item":{"type":"hookPrompt","id":"i0"}}}"#
        ));
        assert!(!says(br#"{"method":"thread/status/changed","params":{}}"#));
        assert!(!says(
            br#"{"method":"account/rateLimits/updated","params":{}}"#
        ));
        assert!(!says(
            br#"{"method":"thread/tokenUsage/updated","params":{}}"#
        ));
        assert!(!says(br#"{"method":"turn/completed","params":{}}"#));
        assert!(!says(b"not json"));
        // the model's own
        assert!(says(
            br#"{"method":"item/started","params":{"item":{"type":"reasoning","id":"i2"}}}"#
        ));
        assert!(says(
            br#"{"method":"item/agentMessage/delta","params":{"delta":"x"}}"#
        ));
        assert!(says(
            br#"{"method":"item/completed","params":{"item":{"type":"agentMessage","id":"i3"}}}"#
        ));
        assert!(says(
            br#"{"method":"item/commandExecution/outputDelta","params":{"delta":"x"}}"#
        ));
        // cut at the reader's cap: read by its start
        assert!(codex_speaks(
            br#"{"method":"item/completed","params":{"item":{"type":"agentMessage","text":"aaa"#,
            true
        ));
        assert!(!codex_speaks(
            br#"{"method":"item/started","params":{"item":{"type":"userMessage","content":[{"text":"aaa"#,
            true
        ));
        assert!(!codex_speaks(
            br#"{"method":"turn/diff/updated","params":{"diff":"aaa"#,
            true
        ));
    }
}
