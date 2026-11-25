#!/usr/bin/env bash
# aib-knives: os=unix
# N-now slices 1–3 knives NW1–NW22 (7b81e 11-18, 519d0 05:24 ruling): a seat writes only its own
# short one-line status, never names another seat, never reads the board, and the journal keeps
# lengths and hashes, never the words. Each rule, broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
nu() { python3 -I scripts/knife.py crates/aibd/src/seat_now.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "seat_now::tests::$3"; }
ne() { python3 -I scripts/knife.py crates/aibd/src/seat_now.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_now "$3"; }
ni() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_now "$3"; }
ns() { python3 -I scripts/knife.py crates/aibd/src/sessions.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_now "$3"; }
gu() { python3 -I scripts/knife.py crates/aibd/src/now_gate.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "now_gate::tests::$3"; }
ng() { python3 -I scripts/knife.py crates/aibd/src/guard.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_now "$3"; }
# NW1 an unknown field (a seat id) is taken
nu '            return Err(Refused("unknown_field"));' \
   '            continue; // knife' \
   a_line_is_short_one_line_and_has_no_seat_field
# NW2 a second line gets in
nu '                && !s.chars().any(|c| c.is_control() || is_bidi_control(c)) =>' \
   '                && !s.chars().any(is_bidi_control) => // knife' \
   a_line_is_short_one_line_and_has_no_seat_field
# NW3 no limit on the numbers
nu '            .filter(|n| *n <= max)' \
   '            .filter(|n| *n <= max || true) // knife (max stays used: -D warnings)' \
   a_line_is_short_one_line_and_has_no_seat_field
# NW4 the journal keeps the words
nu '            "item": text(Some(&n.item)),' \
   '            "item": n.item, // knife' \
   the_journal_has_numbers_and_hashes_never_the_words
# NW5 clear keeps the line
nu '                g.lines.remove(seat_id);' \
   '                let _ = seat_id; // knife' \
   the_board_keeps_one_line_per_seat_and_clear_removes_it
# NW6 a seat's seat_now goes to seat_send (no dispatch)
ni '                Some(crate::seat_now::KIND | crate::seat_now::CHECK | crate::seat_now::MINE)' \
   '                Some(crate::seat_now::CHECK | crate::seat_now::MINE) // knife' \
   a_seat_writes_only_its_own_line_and_only_a_page_reads_the_board
# NW7 a seat token reads the board
ng '            || (path == ACT && *req.method() == Method::POST);' \
   '            || (path == ACT && *req.method() == Method::POST) || path == NOW; // knife' \
   a_seat_writes_only_its_own_line_and_only_a_page_reads_the_board
# --- slice 2: the Stop hook's check and gate
# NW8 a write before this turn counts
nu '        .is_some_and(|w| w.get(..19) >= since.get(..19));' \
   '        .is_some_and(|w| w.get(..19) >= since.get(..19) || true); // knife' \
   the_check_says_written_since_the_turn_and_a_done_stage_needs_its_review
# NW9 a done stage never needs its review
nu '            && (n.track.is_none() || n.why.is_none() || n.idea.is_none())' \
   '            && false // knife' \
   the_check_says_written_since_the_turn_and_a_done_stage_needs_its_review
# NW10 a tool result starts a turn
gu '        Value::Array(blocks) => !blocks.iter().any(|b| b["type"] == "tool_result"),' \
   '        Value::Array(_) => true, // knife' \
   the_turn_starts_at_the_last_person_row_not_at_a_tool_result_or_a_notice
# NW11 a task notification starts a turn
gu '    if row["origin"]["kind"] == "task-notification" {' \
   '    if false && row["origin"]["kind"] == "task-notification" { // knife' \
   the_turn_starts_at_the_last_person_row_not_at_a_tool_result_or_a_notice
# NW12 a row split across chunks is lost
gu '            (lines[0].to_vec(), &lines[1..])' \
   '            (Vec::new(), &lines[..]) // knife' \
   a_long_transcript_is_read_back_across_chunks
# NW13 the gate blocks the same turn again (Claude Code's stop_hook_active)
gu '    if hook["stop_hook_active"] == true {' \
   '    if false && hook["stop_hook_active"] == true { // knife' \
   work_without_an_update_is_blocked_and_everything_unsure_lets_go
# NW14 aibd not answering blocks every seat (fail closed)
gu '    let check = check.ok()?;' \
   '    let check = check.unwrap_or(&Value::Null); // knife' \
   work_without_an_update_is_blocked_and_everything_unsure_lets_go
# --- slice 3: the SessionStart hook reads the seat's own line back
# NW15 seat_now_mine takes a field (a seat id)
nu '    if obj.keys().any(|k| k != "type") {' \
   '    if false && obj.keys().any(|k| k != "type") { // knife' \
   a_seat_reads_back_only_its_own_line
# NW16 seat_now_mine hands back another seat's line
nu '    Ok(json!({ "now": board.get(seat_id) }))' \
   '    Ok(json!({ "now": board.all().into_values().find(|_| !seat_id.is_empty()) })) // knife' \
   a_seat_reads_back_only_its_own_line
# NW17 seat_now_mine is not dispatched (goes to seat_send)
ni '                Some(crate::seat_now::KIND | crate::seat_now::CHECK | crate::seat_now::MINE)' \
   '                Some(crate::seat_now::KIND | crate::seat_now::CHECK) // knife' \
   a_seat_writes_only_its_own_line_and_only_a_page_reads_the_board
# NW18 the line is printed on Stop too
gu '    if hook["hook_event_name"] != "SessionStart" {' \
   '    if false && hook["hook_event_name"] != "SessionStart" { // knife' \
   a_session_start_prints_the_seats_own_line_or_how_to_begin
# NW19 an answer that is not seat_now_mine's is printed as "no line yet"
gu '    let now = mine.get("now")?; // an answer without it is not from seat_now_mine' \
   '    let now = mine.get("now").unwrap_or(&Value::Null); // knife' \
   a_session_start_prints_the_seats_own_line_or_how_to_begin
# --- 0eed7 PRECHECK-chain4 L1 / N2
# NW20 a stopped seat's line stays on the board
ns '                crate::seat_now::board().forget(seat);' \
   '                // knife: the line stays' \
   a_stopped_seat_leaves_the_board
# NW21 a Bidi override gets into a line
nu "    matches!(c, '\\u{202A}'..='\\u{202E}'" \
   "    false && matches!(c, '\\u{202A}'..='\\u{202E}'" \
   a_line_is_short_one_line_and_has_no_seat_field
# NW22 a user folder page reads a seat's words (0eed7 PRECHECK-chain4 N1)
nu '                now[k] = hidden;' \
   '                now["knife"] = hidden; // knife: the words stay' \
   a_user_page_sees_the_line_without_why_or_idea
