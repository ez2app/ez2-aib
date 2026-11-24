#!/usr/bin/env bash
# aib-knives: os=unix
# S89-live-2 knives CX1–CX8 (b4297 11-18, DESIGN-s89live2-codex-pipe-b4297-1118 r3; 519d0 wave 2):
# a codex seat starts safe only, with the user's codex set-up kept out, on the measured version;
# aibd speaks app-server: opening, turn/start on codex's thread, turn/completed ends a turn,
# codex's requests are refused. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
cxp() { python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-seatopen --locked --lib "$3"; }
cw() { python3 -I scripts/knife.py crates/aibd/src/codex_wire.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
cs() { python3 -I scripts/knife.py crates/aibd/src/sessions.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_codex "$3"; }
co() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_codex "$3"; }
# CX1 a dgrs codex seat starts (it would read the user's AGENTS.md and run its start-up)
cxp 'const CODEX_MODES: &[&str] = &["safe", "work"];' \
   'const CODEX_MODES: &[&str] = &["dgrs", "safe", "work"]; // knife' \
   codex_starts_safe_only_with_the_users_set_up_kept_out
# CX2 the user's hooks run in a codex seat
cxp '            for kv in CODEX_OFF.iter().chain(by_mode).chain(CODEX_NO_CODE) {' \
   '            for kv in CODEX_OFF.iter().skip(1).chain(by_mode).chain(CODEX_NO_CODE) { // knife' \
   codex_starts_safe_only_with_the_users_set_up_kept_out
# CX3 a codex version not measured is taken
cxp '            .strip_prefix("codex-cli 0.167.")' \
   '            .strip_prefix("codex-cli 0.").map(|r| r.rsplit(".").next().unwrap_or(r)) // knife' \
   only_the_measured_codex_version_is_taken
# CX4 a text is written before codex named its thread
cw '        let thread = self.thread.get()?;' \
   '        let thread = self.thread.get().map_or("", String::as_str); // knife' \
   a_text_waits_for_the_thread_then_goes_as_turn_start
# CX5 a request codex makes of aibd goes unanswered
cw '            (Some(id), Some(_)) => {' \
   '            (Some(id), Some(_)) if false => { // knife' \
   turn_completed_ends_a_turn_and_requests_are_refused
# CX6 a codex seat gets claude's stream-json line, not turn/start
cs '        let line = match pipe.codex.as_ref() {' \
   '        let line = match None::<&Arc<crate::codex_wire::Codex>> { // knife' \
   a_codex_seat_is_spoken_to_in_its_protocol
# CX7 the opening is never written
cs '            if let Some(o) = &opening {' \
   '            if let Some(o) = opening.as_ref().filter(|_| false) { // knife' \
   a_codex_seat_is_spoken_to_in_its_protocol
# CX8 seats/open lets a dgrs codex seat be written (refused only once its session fails)
co '    if !aib_seatopen::pipe_modes(&r.provider).contains(&r.mode.as_str()) {' \
   '    if false && !aib_seatopen::pipe_modes(&r.provider).contains(&r.mode.as_str()) { // knife' \
   a_dgrs_codex_seat_is_refused_and_version_says_safe_only
