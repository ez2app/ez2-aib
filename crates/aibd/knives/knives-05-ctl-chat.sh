#!/usr/bin/env bash
# aib-knives: os=unix
# aibd ctl chat knives CH1–CH4 (b4297 11-18, 519d0 dispatch S89-live-chat; Andy 03:1x: pair → open → say →
# watch printed raw pages and needed two ids copied by hand). Each rule, broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ch() { python3 -I scripts/knife.py crates/aibd/src/ctl.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --bin aibd "$3"; }
# CH1 what the AI printed at start-up is shown as an answer
ch '    let (_, mut cursor) = output_after(c, session, 0)?;' \
   '    let mut cursor = 0_i64; // knife' \
   chat_pairs_opens_a_safe_seat_talks_and_stops_it
# CH2 chat opens a dgrs seat
ch '    let v = open_persona_answer(c, &["claude", "safe", persona], cards, env, Some(&cwd))?' \
   '    let v = open_persona_answer(c, &["claude", "dgrs", persona], cards, env, Some(&cwd))? // knife' \
   chat_pairs_opens_a_safe_seat_talks_and_stops_it
# CH3 an unpaired terminal is not paired first
ch '    if !keep.exists() {' \
   '    if false { // knife' \
   chat_pairs_opens_a_safe_seat_talks_and_stops_it
# CH4 a result line does not end the AI's turn
ch '        v.get("type").and_then(Value::as_str) == Some("result"),' \
   '        false, // knife' \
   a_chat_line_gives_only_the_ai_text_and_the_end_of_its_turn
# CH5 (S89-live-10) a kept credential aibd no longer knows ends the chat instead of pairing again
ch '            Err(e) if unauthorized(&e) => {' \
   '            Err(e) if false && unauthorized(&e) => { // knife' \
   chat_pairs_again_when_the_kept_credential_died_and_resends_the_line
# CH6 (S89-live-chat-hint) Windows is told to type open, which it does not have
ch '    let opener = if windows { "explorer" } else { "open" };' \
   '    let opener = if windows { "open" } else { "open" }; // knife' \
   the_chat_banner_names_the_persona_the_folder_and_how_to_open_it
# CH7 (0eed7 L1 on d51d06a3) a seat stopped by the restart is reported as a bare send error
ch '                        if e.starts_with("HTTP 409:") && e.contains("seat_not_running") {' \
   '                        if false && e.contains("seat_not_running") { // knife' \
   chat_says_the_seat_stopped_with_the_restart_when_the_resend_finds_no_seat
# CH8 (0eed7 L1 VERIFY-autotest / 519d0 04:00) no folder ⇒ the terminal's folder again
ch '        None => chat_default_folder(&uid, env)?,' \
   '        None => { let _ = (&uid, chat_default_folder); std::env::current_dir().map_err(|e| e.to_string())? } // knife' \
   chat_without_a_folder_opens_in_the_personas_own_folder_under_the_state_root
# CH9 (0eed7 N1 VERIFY-autotest) pairing is asked before the persona is checked
ch '    let uid = chat_persona_uid(persona, cards, env)?;' \
   '    if !keep.exists() { pair(c, input, keep)?; } let uid = chat_persona_uid(persona, cards, env)?; // knife' \
   chat_with_an_unknown_persona_says_so_before_pairing
# CH10 (S89-live-4, 0eed7 C) a seat opened queued is not said to be queued
ch '    if let Some(q) = queued_note(&v["session"]) {' \
   '    if let Some(q) = None::<String> { let _ = queued_note; // knife' \
   chat_says_the_seat_is_queued_and_goes_on_when_a_line_meets_the_queue
# CH11 (S89-live-4) a line that meets the queue ends the chat as a send error
ch '            Err(e) if e.starts_with("HTTP 409:") && e.contains("seat_queued") => {' \
   '            Err(e) if false && e.contains("seat_queued") => { // knife' \
   chat_says_the_seat_is_queued_and_goes_on_when_a_line_meets_the_queue
