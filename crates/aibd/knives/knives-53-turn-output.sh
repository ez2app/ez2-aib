#!/usr/bin/env bash
# N-webonly-c44 knives TO1–TO12 (7ebca 11-20): GET /api/v1/usage says each seat's turn under way
# (when it began, when its model last put anything out), so a page can tell "the text arrived
# and the model never answered" from "it is working". A provider's own lines at the start of a
# turn (Claude's init, codex's turn/start answer and echo) are not the model's output.
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
tt() { python3 -I scripts/knife.py crates/aibd/src/turns.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "turns::tests::$3"; }
tv() { python3 -I scripts/knife.py crates/aibd/src/usage.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "usage::tests::$3"; }
ts() { python3 -I scripts/knife.py crates/aibd/src/sessions.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test usage "$3"; }
# TO1 a turn starts with the output of the turn before
tt '                output_at: None,' \
   '                output_at: Some(at.to_owned()), // knife' \
   a_turn_keeps_when_it_began_and_its_last_output
# TO2 output inside a turn is not kept
tt '            t.output_at = Some(at.to_owned());' \
   '            let _ = (t, at); // knife' \
   a_turn_keeps_when_it_began_and_its_last_output
# TO3 a text typed into a turn under way starts it again
tt '        if !g.contains_key(seat) {' \
   '        if !g.contains_key(seat) || true { // knife' \
   a_turn_keeps_when_it_began_and_its_last_output
# TO4 Claude's init line counts as the model's output
tt '        "system" => matches!(sub, "thinking_tokens" | "permission_denied"),' \
   '        "system" => matches!(sub, "thinking_tokens" | "permission_denied" | "init"), // knife' \
   only_the_models_own_lines_are_claude_output
# TO5 any Claude line counts as the model's output
tt '        _ => false,' \
   '        _ => true, // knife' \
   only_the_models_own_lines_are_claude_output
# TO6 the echo of what a codex seat was told counts as the model's output
tt '    is_item && !told' \
   '    is_item // knife' \
   only_the_models_own_items_are_codex_output
# TO7 any codex notification counts as the model's output
tt '        .is_some_and(|m| m.starts_with("item/"));' \
   '        .is_some_and(|m| m.contains(char::from(47))); // knife' \
   only_the_models_own_items_are_codex_output
# TO8 a cut echo of what a codex seat was told counts as the model's output
tt '        return item && !told;' \
   '        return item; // knife' \
   only_the_models_own_items_are_codex_output
# TO9 an older session of the seat says the turn too
tv '                .filter(|s| seen.insert((*s).to_owned()));' \
   '                .filter(|s| seen.insert((*s).to_owned()) || true); // knife' \
   a_usage_row_says_its_seats_turn_under_way
# TO10 a row does not say when the model last put anything out
tv '            v["turn_output_at"] = json!(turn.and_then(|t| t.output_at));' \
   '            v["turn_output_at"] = json!(turn.and_then(|t| t.output_at).filter(|_| false)); // knife' \
   a_usage_row_says_its_seats_turn_under_way
# TO11 a Claude seat: every line is read as the model's output (what 0eed7 hit, on a Claude seat)
ts '                        if crate::turns::claude_speaks(line, cut) {' \
   '                        if crate::turns::claude_speaks(line, cut) || true { // knife' \
   a_turn_the_model_never_answers_says_started_and_no_output
# TO12 a codex seat: every line is read as the model's output (the two turn_aborted turns)
ts '                        if crate::turns::codex_speaks(line, cut) {' \
   '                        if crate::turns::codex_speaks(line, cut) || true { // knife' \
   a_turn_the_model_never_answers_says_started_and_no_output
