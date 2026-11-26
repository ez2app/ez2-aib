#!/usr/bin/env bash
# aib-knives: os=macos   # 90856 11-25 E3 r2: a test its knives expect red already fails on ubuntu unmutated (false bite there)
# S89-live-4 / S89-live-5 knives LQ1–LQ3, LP1–LP2 (7b81e 11-18, 519d0 04:0x dispatch; 0eed7 VERIFY-s89live-0eed7-a7d973ed
# C and J2). A fifth seat while four run says it is queued and the limit; a provider aibd cannot start over pipes is
# refused before a seat is written. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no ${5:-default}): one wrapper per test file
ki() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test seat_input "$4"; }
ko() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked --test seats_open "$4"; }
# LQ1 a text to a queued seat is said as "not running"
ki crates/aibd/src/sessions.rs \
  '                return Err(if queued {' \
  '                return Err(if false && queued { // knife' \
  a_queued_seat_says_it_is_queued_and_the_limit
# LQ2 the queued refusal does not say how many seats run at once
ki crates/aibd/src/seat_input.rs \
  '                    "the seat is queued: all {max} slots are in use (at most {max} seats run at once); nothing was written, send again once it runs"' \
  '                    "the seat is queued; nothing was written ({max})" // knife' \
  a_queued_seat_says_it_is_queued_and_the_limit
# LQ3 the open answer of a queued seat carries no place or slots
ko crates/aibd/src/seats.rs \
  '            let rec = if rec.state == "pending" {' \
  '            let rec = if false && rec.state == "pending" { // knife' \
  a_queued_seat_session_starts_when_a_slot_frees
# LP1 a provider that cannot start over pipes still gets a seat written
ko crates/aibd/src/seats.rs \
  '    if !pipe_supported(&r.provider) {' \
  '    if false && !pipe_supported(&r.provider) { // knife' \
  a_provider_that_cannot_start_is_refused_and_leaves_no_seat
# LP2 the check asks a fixed list instead of pipe_argv, and agy (a brand aibd cannot start) slips through
ko crates/aibd/src/seats.rs \
  '        Err(aib_seatopen::PipeRefused::Provider(_))' \
  '        Err(aib_seatopen::PipeRefused::Provider(_))) || provider == "agy" || (false // knife' \
  a_provider_that_cannot_start_is_refused_and_leaves_no_seat
