#!/usr/bin/env bash
# S89-input knives SI1–SI3 (a9136 11-18, DECISION-council-seatinput-519d0-1118 §2): aibd builds the one
# stream-json line itself, escaped, at most 32 KiB, no NUL. Each rule, broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
si() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "seat_input::tests::$3"; }
# SI1 the text goes in raw: a newline in it forges a second message
si '    let mut line = msg.to_string().into_bytes();' \
   '    let mut line = format!("{{\"type\":\"user\",\"message\":{{\"role\":\"user\",\"content\":[{{\"type\":\"text\",\"text\":\"{text}\"}}]}}}}", ).into_bytes(); let _ = msg; // knife' \
   a_text_is_one_escaped_line_that_cannot_forge_a_second_message
# SI2 a text over 32 KiB is sent
si '    if text.len() > MAX_TEXT {' \
   '    if text.len() > MAX_TEXT * 2 { // knife' \
   empty_too_long_and_nul_texts_are_refused
# SI3 a NUL is sent
si "    if text.contains('\\0') {" \
   "    if false && text.contains('\\0') { // knife" \
   empty_too_long_and_nul_texts_are_refused
