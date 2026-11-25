#!/usr/bin/env bash
# aib-knives: os=unix
# aib-knives: timeout=120m
# S89-input knives SI1–SI17, PT1–PT3, MC1 (a9136 11-18, DECISION-council-seatinput-519d0-1118 §2): aibd builds the one
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
        -- cargo test -p aibd --locked --test seat_input "$4"; }
# the same, in the dgrs switch's own test process (tests/seat_input_dgrs.rs)
kd() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test seat_input_dgrs "$4"; }
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
kd crates/aibd/src/seat_input.rs \
   '        Some("dgrs") => dgrs_on,' \
   '        Some("dgrs") => false && dgrs_on, // knife' \
   with_the_switch_on_a_dgrs_seat_takes_input_and_says_so
# SI11 a second text waits on the stuck write instead of answering busy at once
ke crates/aibd/src/sessions.rs \
   '        if pipe.busy.swap(true, SeqCst) {' \
   '        if pipe.busy.swap(true, SeqCst) && false { // knife' \
   a_provider_that_stops_reading_gets_busy_not_a_hang
# SI12 the write waits without a limit (the request hangs)
ke crates/aibd/src/sessions.rs \
   '            if !op.tick_or_wake(&done.0, &done.1, &mut seen) {' \
   '            if !op.tick_or_wake(&done.0, &done.1, &mut seen) && false { // knife' \
   a_provider_that_stops_reading_gets_busy_not_a_hang
# SI13 a dgrs seat inherits the ssh agent after all (the withheld names are not taken out)
python3 -I scripts/knife.py crates/aibd/src/sessions.rs \
   '        .filter(|n| !withhold.iter().any(|w| allowlist::env_name_eq(n, w)))' \
   '        .filter(|n| !n.is_empty() || withhold.is_empty()) // knife' \
   --expect-fail a_dgrs_seat_never_inherits_the_ssh_agent_or_node_options_a_safe_seat_does \
   -- cargo test -p aibd --locked --lib seat_input::tests::a_dgrs_seat_never_inherits_the_ssh_agent_or_node_options_a_safe_seat_does
# SI14 a stop with a survivor is also reported as done
python3 -I scripts/knife.py crates/aibd/src/platform/session.rs \
   '    stop["survivors"].as_array().is_some_and(Vec::is_empty)' \
   '    stop["survivors"].as_array().is_some() // knife' \
   --expect-fail a_stop_with_nothing_left_seen_is_done_and_anything_left_is_not \
   -- cargo test -p aibd --locked --bin aibd a_stop_with_nothing_left_seen_is_done_and_anything_left_is_not
# SI15 ctl pair keeps a credential without the person's y
python3 -I scripts/knife.py crates/aibd/src/ctl.rs \
   '    if !(answer.eq_ignore_ascii_case("y") || answer.eq_ignore_ascii_case("yes")) {' \
   '    if false && !(answer.eq_ignore_ascii_case("y") || answer.eq_ignore_ascii_case("yes")) { // knife' \
   --expect-fail pair_without_y_keeps_nothing \
   -- cargo test -p aibd --locked --bin aibd pair_without_y_keeps_nothing
# PT1 a card text that changed since the registry read it is still given to the seat
python3 -I scripts/knife.py crates/aibd/src/personas.rs \
   '    if !hex.eq_ignore_ascii_case(persona_sha) {' \
   '    if false && !hex.eq_ignore_ascii_case(persona_sha) { // knife' \
   --expect-fail the_card_text_must_match_its_hash_and_size \
   -- cargo test -p aibd --locked --lib personas::tests::the_card_text_must_match_its_hash_and_size
# PT2 a card path with .. or a root of its own leaves the vault
python3 -I scripts/knife.py crates/aibd/src/personas.rs \
   '                    .all(|c| matches!(c, std::path::Component::Normal(_)));' \
   '                    .all(|_| true); // knife' \
   --expect-fail the_card_text_path_comes_from_the_registry_root \
   -- cargo test -p aibd --locked --lib personas::tests::the_card_text_path_comes_from_the_registry_root
# PT3 the checked text is never handed to the provider
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
   '        if !aib_seatopen::add_persona_file(&req.provider, a, f) {' \
   '        if false && !aib_seatopen::add_persona_file(&req.provider, a, f) { // knife' \
   --expect-fail a_seat_gets_its_persona_text_and_a_changed_card_is_refused \
   -- cargo test -p aibd --locked --test seats_open a_seat_gets_its_persona_text_and_a_changed_card_is_refused
# SI16 a text not taken within 5 s is said to be written (or refused) instead of pending
ke crates/aibd/src/sessions.rs \
   '                    late: true,' \
   '                    late: false, // knife' \
   a_provider_that_stops_reading_gets_busy_not_a_hang
# SI17 a dgrs seat is given the whole policy like a safe seat
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
   '    if mode != "safe" {' \
   '    if false && mode != "safe" { // knife' \
   --expect-fail a_dgrs_seat_never_inherits_the_ssh_agent_or_node_options_a_safe_seat_does \
   -- cargo test -p aibd --locked --lib seat_input::tests::a_dgrs_seat_never_inherits_the_ssh_agent_or_node_options_a_safe_seat_does
# MC1 a seat loads the account-level connectors again (no --strict-mcp-config)
python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs \
   '                NO_CONNECTORS,' \
   '                "--verbose", // knife' \
   --expect-fail a_dry_run_plan_shows_the_argv_and_the_env_names \
   -- cargo test -p aibd --locked --test seats_open a_dry_run_plan_shows_the_argv_and_the_env_names
