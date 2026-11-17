#!/usr/bin/env bash
# S89-input knives SI1–SI10 (a9136 11-18, DECISION-council-seatinput-519d0-1118 §2): aibd builds the one
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
# end to end (crates/aibd/tests/seat_input.rs, fake provider)
ke() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test "${5:-seat_input}" "$4"; }
# SI4 the guard keeps a confirmed page off act: nothing can be typed
ke crates/aibd/src/guard.rs \
   '    if !reads && path != PAIR_REVOKE && path != ACT {' \
   '    if !reads && path != PAIR_REVOKE { // knife' \
   two_texts_reach_the_one_running_provider_one_line_each
# SI5 a dgrs seat takes input
ke crates/aibd/src/seat_input.rs \
   '        Some("dgrs") => dgrs_on,' \
   '        Some("dgrs") => true || dgrs_on, // knife' \
   a_dgrs_seat_takes_no_input
# SI6 the text is journaled
ke crates/aibd/src/seat_input.rs \
   '            "len": len,' \
   '            "len": len, "text": text, // knife' \
   the_event_keeps_length_and_hash_never_the_text
# SI7 the event says the person was verified (7b81e NOTE-actauth: today it never is)
ke crates/aibd/src/seat_input.rs \
   '            "verified_person": false,' \
   '            "verified_person": true, // knife' \
   the_event_keeps_length_and_hash_never_the_text
# SI8 a revoked page still types (the revoke ends nothing)
ke crates/aibd/src/pair_api.rs \
   '    p.revoke_credential(&a.digest);' \
   '    let _ = (&p, &a); // knife' \
   only_a_confirmed_page_types
# SI9 the switch is on for any value (S89-dgrs: exactly 1)
si '    v == Some("1")' \
   '    v.is_some() // knife' \
   dgrs_takes_input_only_when_this_machine_turned_it_on
# SI10 dgrs ignores the switch and never takes input
ke crates/aibd/src/seat_input.rs \
   '        Some("dgrs") => dgrs_on,' \
   '        Some("dgrs") => false && dgrs_on, // knife' \
   with_the_switch_on_a_dgrs_seat_takes_input_and_says_so seat_input_dgrs
