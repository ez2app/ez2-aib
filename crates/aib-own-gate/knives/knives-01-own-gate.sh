#!/usr/bin/env bash
# The "aib-own-gate knives (macOS)" step of aibd.yml's rust job, moved here word for word (11-16 裁定:
# 新刀放 crates/<套件>/knives/;IFACE-drive-shadow-519d0-1117 §5; #804's shape). aibd.yml runs every
# crates/aib-own-gate/knives/*.sh in name order in one step; aib-xtask knife-anchors reads them.
# Run from the repo root; -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# S79 o5 own-gate (b433b): each turn-gate rule, broken, turns its own test red.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
gk() { python3 -I scripts/knife.py "crates/aib-own-gate/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-own-gate --locked "$4"; }
# OW4 a turn with no progress is let through
gk lib.rs '    if held >= 1 {' '    if held >= 1 || held == 0 { // knife' \
   a_turn_with_no_progress_is_held_once_then_let_through_with_a_blocked_line
# OW5 the second try ends without a blocked line
gk lib.rs '        Verdict::Pass(Some(blocked))' '        Verdict::Pass(None) // knife' \
   a_turn_with_no_progress_is_held_once_then_let_through_with_a_blocked_line
# OW9 a missing turn start is not written as unreadable
gk lib.rs '            "unreadable",' '            "blocked", // knife' \
   a_fault_never_holds_a_seat
# OW11 a manual line lets the turn end
gk lib.rs 'pub const PROGRESS_KINDS: [&str; 3] = ["done", "progress", "none"];' 'pub const PROGRESS_KINDS: [&str; 4] = ["done", "progress", "none", "manual"]; // knife' \
   only_a_done_progress_or_none_of_this_seat_and_turn_lets_it_end
# OG1 a seat with no turn start is held
gk lib.rs '    let Some(turn) = current_turn(lines, seat) else {' '    let Some(turn) = current_turn(lines, seat).or(Some(String::new())) else { // knife' \
   a_fault_never_holds_a_seat
# OG2 turn-start does not write the turn start (falls through to stop)
gk main.rs '    if cmd == "turn-start" {' '    if cmd == "turn-start" && false { // knife' \
   a_gated_seat_is_held_once_and_turn_start_never_holds
# OG3 a transcript tail after the turn start is taken as no tool call
gk lib.rs '    if n > 0 || complete || earliest.is_some_and(|e| e <= start_ms) {' '    if n > 0 || complete || earliest.is_some_and(|e| e <= start_ms) || true { // knife' \
   tools_are_counted_from_the_turn_start_by_timestamp
# OG4 tool calls before the turn start are counted
gk lib.rs '        if at < start_ms || s(&v, "type") != "assistant" {' '        if s(&v, "type") != "assistant" { // knife' \
   tools_are_counted_from_the_turn_start_by_timestamp
# OT1 (o5) a task notification is taken as a prompt nobody can place
gk lib.rs '            Some("task_notification") => continue,' '            Some("task_notification") => return TRIGGER_UNKNOWN, // knife' \
   the_turn_origin_of_the_last_prompt_says_who_started_the_turn
# OT2 a round line before the turn start counts
gk lib.rs '                .is_some_and(|t| t >= start_ms)' '                .is_some_and(|t| t >= start_ms - 1_000_000) // knife' \
   a_round_line_counts_for_its_seat_from_the_turn_start
# OT3 a missing round line lets the turn end
gk lib.rs '    let round_ok = round != Round::Missing;' '    let round_ok = round != Round::Missing || true; // knife' \
   a_turn_autopush_started_needs_its_round_line
# OT4 a short 自動推進 turn passes as a reply
gk lib.rs '    if round_ok && tools == Some(0) && now_ms - started < REPLY_MS {' '    if tools == Some(0) && now_ms - started < REPLY_MS { // knife' \
   a_turn_autopush_started_needs_its_round_line
# OG5 the stop hook does not look for the round line
gk main.rs '        (Some(st), Some((t, _))) if trigger_of(t) == TRIGGER_AUTO => {' '        (Some(st), Some((t, _))) if trigger_of(t) == TRIGGER_AUTO && false => { // knife' \
   a_turn_autopush_started_is_held_until_its_round_line
# OG14 (envelope r3) the stop hook does not see a v 2 round line (ev, no kind)
gk lib.rs '            && (s(l, "ev") == "round" || s(l, "kind") == "round")' '            && s(l, "kind") == "round" // knife' \
   a_round_line_counts_for_its_seat_from_the_turn_start
# OG15 the stop hook does not read a v 2 round line's occurred_at
gk lib.rs '                .and_then(aib_ledger::envelope::parse_rfc3339_ms)' '                .and_then(|_| None::<i64>) // knife' \
   a_round_line_counts_for_its_seat_from_the_turn_start
