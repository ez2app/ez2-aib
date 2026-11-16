//! S79-own o-3 (IFACE-s79-own-gate-a9136-1116 r1 §2): what the stop hook decides.
use aib_own_gate::*;
use serde_json::{json, Value};

const SEAT: &str = "519d0";
const T0: i64 = 1_000_000;

fn start() -> Value {
    turn_start_line(SEAT, T0, None)
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
    let v = decide(
        Ok(&l),
        SEAT,
        Some(3),
        Round::NotNeeded,
        "first-item",
        T0 + 300_000,
    );
    assert_eq!(kind_of(&v), ("hold", "blocked".into()), "{v:?}");
    let Verdict::Hold(b) = v else { unreachable!() };
    assert_eq!(b["n"], 1);
    assert_eq!(b["turn_id"], turn());
    assert_eq!(b["first"], "first-item");
    l.push(b);
    let v = decide(
        Ok(&l),
        SEAT,
        Some(3),
        Round::NotNeeded,
        "first-item",
        T0 + 360_000,
    );
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
            decide(Ok(&l), SEAT, Some(9), Round::NotNeeded, "f", T0 + 300_000),
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
        kind_of(&decide(
            Ok(&l),
            SEAT,
            Some(9),
            Round::NotNeeded,
            "f",
            T0 + 300_000
        )),
        ("hold", "blocked".into())
    );
}

/// OW9 and the other faults: an unreadable ledger, no turn start — the turn always ends
/// and the line says why.
#[test]
fn a_fault_never_holds_a_seat() {
    let v = decide(
        Err("disk full".into()),
        SEAT,
        Some(1),
        Round::NotNeeded,
        "f",
        T0,
    );
    assert_eq!(kind_of(&v), ("pass", "fault".into()));
    let v = decide(Ok(&[]), SEAT, Some(1), Round::NotNeeded, "f", T0);
    assert_eq!(kind_of(&v), ("pass", "unreadable".into()));
    let other = vec![turn_start_line("a9136", T0, None)];
    let v = decide(Ok(&other), SEAT, Some(1), Round::NotNeeded, "f", T0);
    assert_eq!(kind_of(&v), ("pass", "unreadable".into()));
}

/// A reply (no tool call, under two minutes) ends with exempt_reply; a long turn or a
/// transcript not read is not taken as a reply.
#[test]
fn a_short_turn_with_no_tool_call_is_a_reply() {
    let l = vec![start()];
    let v = decide(Ok(&l), SEAT, Some(0), Round::NotNeeded, "f", T0 + 30_000);
    assert_eq!(kind_of(&v), ("pass", "exempt_reply".into()));
    let v = decide(
        Ok(&l),
        SEAT,
        Some(0),
        Round::NotNeeded,
        "f",
        T0 + REPLY_MS + 1,
    );
    assert_eq!(kind_of(&v), ("hold", "blocked".into()));
    let v = decide(Ok(&l), SEAT, None, Round::NotNeeded, "f", T0 + 30_000);
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

/// Tool calls are counted from the turn's start by timestamp; a message that arrives
/// mid-turn (a user entry) does not reset the count; a tail that starts after the turn
/// began and shows no call is "cannot tell" (OG3).
#[test]
fn tools_are_counted_from_the_turn_start_by_timestamp() {
    let st = iso_ms("2026-11-16T01:00:00.000Z").unwrap();
    let t = [
        json!({"type": "user", "timestamp": "2026-11-16T00:59:00.000Z", "message": {"content": "old prompt"}}),
        json!({"type": "assistant", "timestamp": "2026-11-16T00:59:30.000Z", "message": {"content": [{"type": "tool_use"}, {"type": "tool_use"}]}}),
        json!({"type": "user", "timestamp": "2026-11-16T01:00:00.000Z", "message": {"content": "new prompt"}}),
        json!({"type": "assistant", "timestamp": "2026-11-16T01:00:05.000Z", "message": {"content": [{"type": "text"}, {"type": "tool_use"}]}}),
        json!({"type": "user", "timestamp": "2026-11-16T01:00:06.000Z", "message": {"content": "a message mid-turn"}}),
        json!({"type": "assistant", "timestamp": "2026-11-16T01:00:09.000Z", "message": {"content": [{"type": "tool_use"}]}}),
    ]
    .iter()
    .map(Value::to_string)
    .collect::<Vec<_>>()
    .join("\n");
    assert_eq!(tools_since(&t, st, false), Some(2));
    let only_new = "{\"type\":\"user\",\"timestamp\":\"2026-11-16T01:00:01Z\",\"message\":{\"content\":\"hi\"}}";
    assert_eq!(tools_since(only_new, st, true), Some(0));
    assert_eq!(
        tools_since(only_new, st, false),
        None,
        "a tail after the start with no call cannot tell"
    );
    let old_too =
        format!("{{\"type\":\"user\",\"timestamp\":\"2026-11-16T00:00:00Z\"}}\n{only_new}");
    assert_eq!(tools_since(&old_too, st, false), Some(0));
}

/// iso_ms reads Claude Code's timestamps and refuses anything else.
#[test]
fn iso_ms_reads_utc_timestamps_only() {
    assert_eq!(iso_ms("1970-01-01T00:00:00Z"), Some(0));
    assert_eq!(iso_ms("1970-01-01T00:00:01.5Z"), Some(1500));
    assert_eq!(iso_ms("2026-11-16T01:13:32.123Z"), Some(1_794_791_612_123));
    assert_eq!(iso_ms("2000-03-01T00:00:00Z"), Some(951_868_800_000));
    for bad in [
        "",
        "2026-11-16 01:13:32Z",
        "2026-11-16T01:13:32+08:00",
        "2026-13-01T00:00:00Z",
        "2026-11-16T01:13:32.Z",
        "２026-11-16T01:13:32Z",
    ] {
        assert_eq!(iso_ms(bad), None, "{bad}");
    }
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

// ---- S79-own o5: who started the turn, and the round line a 自動推進 turn needs ----

/// Transcript lines shaped like Claude Code's (keys copied from a seat's real file; the
/// words are made up).
fn scheduled() -> String {
    json!({"type": "user", "isMeta": true, "promptSource": "system", "turnOrigin": "scheduled",
           "scheduledTaskId": "38a26472", "timestamp": "2026-11-16T03:03:00.000Z",
           "message": {"role": "user", "content": "自動推進(定時)"}})
    .to_string()
}
fn human() -> String {
    json!({"type": "user", "origin": {"kind": "human"}, "promptSource": "typed",
           "turnOrigin": "human", "timestamp": "2026-11-16T03:04:00.000Z",
           "message": {"role": "user", "content": "照建議做"}})
    .to_string()
}
fn notification() -> String {
    json!({"type": "user", "promptSource": "system", "turnOrigin": "task_notification",
           "timestamp": "2026-11-16T03:05:00.000Z",
           "message": {"role": "user", "content": "<task-notification>…</task-notification>"}})
    .to_string()
}
fn tool_result() -> String {
    json!({"type": "user", "toolUseResult": {"stdout": "ok"}, "timestamp": "2026-11-16T03:05:01.000Z",
           "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]}})
    .to_string()
}
fn meta() -> String {
    json!({"type": "user", "isMeta": true, "timestamp": "2026-11-16T03:05:02.000Z",
           "message": {"role": "user", "content": "<system-reminder>…</system-reminder>"}})
    .to_string()
}
fn assistant() -> String {
    json!({"type": "assistant", "timestamp": "2026-11-16T03:05:03.000Z",
           "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash"}]}})
    .to_string()
}

/// OT1: the last prompt's turnOrigin decides; tool results, meta lines and task
/// notifications in between do not; a prompt without the field is unknown.
#[test]
fn the_turn_origin_of_the_last_prompt_says_who_started_the_turn() {
    let t = [
        scheduled(),
        assistant(),
        tool_result(),
        meta(),
        notification(),
        tool_result(),
    ]
    .join("\n");
    assert_eq!(trigger_of(&t), TRIGGER_AUTO);
    let t = [
        scheduled(),
        assistant(),
        human(),
        assistant(),
        tool_result(),
    ]
    .join("\n");
    assert_eq!(trigger_of(&t), TRIGGER_HUMAN);
    let t = [human(), notification(), assistant()].join("\n");
    assert_eq!(
        trigger_of(&t),
        TRIGGER_HUMAN,
        "a notification follows the prompt before it"
    );
    let old = json!({"type": "user", "message": {"role": "user", "content": "/model"}}).to_string();
    assert_eq!(trigger_of(&[scheduled(), old].join("\n")), TRIGGER_UNKNOWN);
    assert_eq!(
        trigger_of(&[tool_result(), meta()].join("\n")),
        TRIGGER_UNKNOWN
    );
    assert_eq!(trigger_of(""), TRIGGER_UNKNOWN);
}

/// OT2: a round line counts for its seat at or after the turn start only.
#[test]
fn a_round_line_counts_for_its_seat_from_the_turn_start() {
    let r = vec![
        json!({"v": 1, "kind": "round", "seat": SEAT, "at_ms": T0 - 1}),
        json!({"v": 1, "kind": "round", "seat": "a9136", "at_ms": T0 + 5}),
    ];
    assert!(!round_logged(&r, SEAT, T0));
    let mut r = r;
    r.push(json!({"v": 1, "kind": "round", "seat": SEAT, "at_ms": T0}));
    assert!(round_logged(&r, SEAT, T0));
    // the aib-ledger envelope v 2 shape aib-drive writes now: ev + occurred_at, no kind / at_ms
    let v2 = |seat: &str, ms: i64| {
        json!({"v": 2, "ev": "round", "seat": seat,
               "occurred_at": aib_ledger::envelope::rfc3339_ms(ms)})
    };
    assert!(!round_logged(
        &[v2(SEAT, T0 - 1), v2("a9136", T0 + 5)],
        SEAT,
        T0
    ));
    assert!(round_logged(&[v2(SEAT, T0)], SEAT, T0));
}

/// OT3: a 自動推進 turn without its round line is held even with a done line or as a
/// short reply, and lets go the second time; with the round line it ends as before.
#[test]
fn a_turn_autopush_started_needs_its_round_line() {
    let done = json!({"v": 1, "at_ms": T0 + 5, "seat": SEAT, "kind": "done", "turn_id": turn()});
    let l = vec![start(), done.clone()];
    let v = decide(Ok(&l), SEAT, Some(4), Round::Missing, "f", T0 + 300_000);
    let Verdict::Hold(b) = v.clone() else {
        panic!("{v:?}")
    };
    assert_eq!(b["missing"], json!(["round"]));
    assert_eq!(
        decide(Ok(&l), SEAT, Some(4), Round::Logged, "f", T0 + 300_000),
        Verdict::Pass(None)
    );
    let l = vec![start()];
    let v = decide(Ok(&l), SEAT, Some(0), Round::Missing, "f", T0 + 30_000);
    let Verdict::Hold(b) = v.clone() else {
        panic!("a short turn is no reply here: {v:?}")
    };
    assert_eq!(b["missing"], json!(["own", "round"]));
    let l = vec![start(), done, b];
    let v = decide(Ok(&l), SEAT, Some(0), Round::Missing, "f", T0 + 40_000);
    assert_eq!(kind_of(&v), ("pass", "blocked".into()), "{v:?}");
}

/// OT4: the turn start keeps the transcript path the hook gave, and it is read back.
#[test]
fn the_turn_start_keeps_the_transcript_path() {
    let l = vec![turn_start_line(SEAT, T0, Some("/tmp/x.jsonl"))];
    assert_eq!(
        current_turn_transcript(&l, SEAT).as_deref(),
        Some("/tmp/x.jsonl")
    );
    let l = vec![turn_start_line(SEAT, T0, None)];
    assert_eq!(current_turn_transcript(&l, SEAT), None);
}
