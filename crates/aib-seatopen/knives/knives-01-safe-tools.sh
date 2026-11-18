#!/usr/bin/env bash
# aib-seatopen knives SO-T1 (b4297 11-18, 5d33a REVIEW-S89-talk1-5d33a-67211a25 M1): a safe claude seat gets
# no built-in tool (`--tools ""`). Each rule, broken, turns its own test red. Run from the repo root.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form)
st() { python3 -I scripts/knife.py crates/aib-seatopen/src/pipe.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-seatopen --locked --lib "pipe::tests::$3"; }
# SO-T1 a safe seat keeps claude's built-in tools (Read / Grep / Glob need no approval in -p's default mode)
st '            if mode == "safe" {' \
   '            if mode == "safe-not" { // knife' \
   a_safe_claude_seat_gets_no_built_in_tool
