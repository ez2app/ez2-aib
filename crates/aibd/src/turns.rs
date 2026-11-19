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

use std::collections::HashSet;
use std::sync::Mutex;

/// The seats that are in a turn now.
#[derive(Default)]
pub struct Turns {
    seats: Mutex<HashSet<String>>,
}

impl Turns {
    fn seats(&self) -> std::sync::MutexGuard<'_, HashSet<String>> {
        self.seats.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// A text was written to `seat`: its turn runs until the provider ends it.
    pub fn began(&self, seat: &str) {
        self.seats().insert(seat.to_owned());
    }

    /// `seat`'s provider ended a turn (or its session started anew); whether it was in one.
    pub fn ended(&self, seat: &str) -> bool {
        self.seats().remove(seat)
    }

    pub fn in_turn(&self, seat: &str) -> bool {
        self.seats().contains(seat)
    }
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
}
