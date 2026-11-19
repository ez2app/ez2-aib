#!/usr/bin/env bash
# S89-live-9 knives SN1–SN2 (b4297 11-18, 0eed7 REVIEW-s89-input L2): only a seat's provider
# gets a stdin pipe; any other program gets none. Each rule, broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
sn() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test "$5" "$4"; }
# SN1 every session gets a stdin pipe again: a program that reads to the end waits forever
sn crates/aibd/src/sessions.rs \
   "            input: req.input, // S89-live-9: a stdin pipe only for a seat's provider" \
   '            input: true, // knife' \
   a_program_that_reads_to_the_end_is_not_left_waiting_on_a_stdin_pipe sessions
# SN2 a seat's provider gets no stdin: nothing typed reaches it
sn crates/aibd/src/seats.rs \
   '        input: true,' \
   '        input: false, // knife' \
   two_texts_reach_the_one_running_provider_one_line_each seat_input
