//! S79-own o-3 (IFACE-s79-own-gate-a9136-1116 r1 §2): what the stop hook decides.
use aib_own_gate::*;
use serde_json::{json, Value};

const SEAT: &str = "519d0";
const T0: i64 = 1_000_000;

fn start() -> Value {
    turn_start_line(SEAT, T0)
}

fn turn() -> String {
    format!("{SEAT}-{T0}")
}

fn kind_of(v: &Verdict) -> (&'static str, String) {
    match v {
        Verdict::Pass(None) => ("pass", String::new()),
        Verdict::Pass(Some(l)) => ("pass", l["kind"].as_str().unwrap_or("").to_owned()),
        Verdict::Hold(l) => ("hold", l["kind"].as_str().unwrap_or("").to_owned()),
    }
}

/// OW4 + OW5: a turn with nothing to show is held once, its second try ends with a second
/// blocked line; both lines carry the turn and the first item.
#[test]
fn a_turn_with_no_progress_is_held_once_then_let_through_with_a_blocked_line() {
    let mut l = vec![start()];
    let v = decide(Ok(&l), SEAT, Some(3), "first-item", T0 + 300_000);
    assert_eq!(kind_of(&v), ("hold", "blocked".into()), "{v:?}");
    let Verdict::Hold(b) = v else { unreachable!() };
    assert_eq!(b["n"], 1);
    assert_eq!(b["turn_id"], turn());
    assert_eq!(b["first"], "first-item");
    l.push(b);
    let v = decide(Ok(&l), SEAT, Some(3), "first-item", T0 + 360_000);
    assert_eq!(kind_of(&v), ("pass", "blocked".into()), "{v:?}");
    let Verdict::Pass(Some(b2)) = v else {
        unreachable!()
    };
    assert_eq!(b2["n"], 2);
}

/// done / progress / none in this turn end it; the same in an earlier turn, another
/// seat's, or a manual line (OW11) do not.
#[test]
fn only_a_done_progress_or_none_of_this_seat_and_turn_lets_it_end() {
    for k in ["done", "progress", "none"] {
        let l = vec![
            start(),
            json!({"v": 1, "at_ms": T0 + 5, "seat": SEAT, "kind": k, "turn_id": turn()}),
        ];
        assert_eq!(
            decide(Ok(&l), SEAT, Some(9), "f", T0 + 300_000),
            Verdict::Pass(None),
            "{k}"
        );
    }
    let l = vec![
        json!({"v": 1, "at_ms": 1, "seat": SEAT, "kind": "done", "turn_id": "519d0-1"}),
        json!({"v": 1, "at_ms": 2, "seat": "a9136", "kind": "done", "turn_id": turn()}),
        json!({"v": 1, "at_ms": 3, "seat": SEAT, "kind": "manual", "turn_id": turn()}),
        start(),
    ];
    assert_eq!(
        kind_of(&decide(Ok(&l), SEAT, Some(9), "f", T0 + 300_000)),
        ("hold", "blocked".into())
    );
}

/// OW9 and the other faults: an unreadable ledger, no turn start — the turn always ends
/// and the line says why.
#[test]
fn a_fault_never_holds_a_seat() {
    let v = decide(Err("disk full".into()), SEAT, Some(1), "f", T0);
    assert_eq!(kind_of(&v), ("pass", "fault".into()));
    let v = decide(Ok(&[]), SEAT, Some(1), "f", T0);
    assert_eq!(kind_of(&v), ("pass", "unreadable".into()));
    let other = vec![turn_start_line("a9136", T0)];
    let v = decide(Ok(&other), SEAT, Some(1), "f", T0);
    assert_eq!(kind_of(&v), ("pass", "unreadable".into()));
}

/// A reply (no tool call, under two minutes) ends with exempt_reply; a long turn or a
/// transcript not read is not taken as a reply.
#[test]
fn a_short_turn_with_no_tool_call_is_a_reply() {
    let l = vec![start()];
    let v = decide(Ok(&l), SEAT, Some(0), "f", T0 + 30_000);
    assert_eq!(kind_of(&v), ("pass", "exempt_reply".into()));
    let v = decide(Ok(&l), SEAT, Some(0), "f", T0 + REPLY_MS + 1);
    assert_eq!(kind_of(&v), ("hold", "blocked".into()));
    let v = decide(Ok(&l), SEAT, None, "f", T0 + 30_000);
    assert_eq!(kind_of(&v), ("hold", "blocked".into()));
}

#[test]
fn the_gated_seats_file_takes_seat_codes_and_skips_comments() {
    assert_eq!(
        gated_seats("# 先只 519d0\n519d0  # Andy 11-16\nnot-a-seat\nABCDE\n\na9136\n"),
        vec!["519d0".to_owned(), "a9136".to_owned()]
    );
}

#[test]
fn a_torn_ledger_line_is_skipped_not_fatal() {
    let text = format!("{}\n{{\"v\":1,\"kind\":\n[1,2]\n", start());
    assert_eq!(parse_ledger(&text).len(), 1);
}

/// Tool calls are counted from the last prompt; tool results (user entries with a
/// tool_result part) do not start a new turn.
#[test]
fn tools_are_counted_since_the_last_prompt() {
    let t = [
        json!({"type": "user", "message": {"content": "old prompt"}}),
        json!({"type": "assistant", "message": {"content": [{"type": "tool_use"}, {"type": "tool_use"}]}}),
        json!({"type": "user", "message": {"content": [{"type": "text", "text": "new prompt"}]}}),
        json!({"type": "assistant", "message": {"content": [{"type": "text"}, {"type": "tool_use"}]}}),
        json!({"type": "user", "message": {"content": [{"type": "tool_result"}]}}),
        json!({"type": "assistant", "message": {"content": [{"type": "tool_use"}]}}),
    ]
    .iter()
    .map(Value::to_string)
    .collect::<Vec<_>>()
    .join("\n");
    assert_eq!(tools_this_turn(&t), 2);
    assert_eq!(
        tools_this_turn("{\"type\":\"user\",\"message\":{\"content\":\"hi\"}}"),
        0
    );
}

/// The seat is the session's last five characters only when a seat file names that
/// session; never guessed from the id alone.
#[test]
fn the_seat_comes_from_a_seat_file_naming_the_session() {
    let id = "d8f54912-b04c-4d9b-9aae-6746dd7a9136";
    let files = vec![json!({"session_uuid": id})];
    assert_eq!(seat_of(id, &files), Some("a9136".into()));
    assert_eq!(seat_of(id, &[json!({"session_uuid": "other"})]), None);
    assert_eq!(seat_of("", &files), None);
}
