#!/usr/bin/env bash
# aib-knives: os=unix
# a work seat in a preview (a9136 11-19, 519d0 12:02 8-hour goal): a preview opens safe and work seats, never dgrs.
# Each rule, broken, turns its own test red. Run from the repo root; -eo pipefail like GitHub's `shell: bash`.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
wp() { python3 -I scripts/knife.py crates/aibd/src/seats.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "seats::tests::$3"; }
# WP1 a work seat is refused in a preview again
wp '    in_preview && mode != "safe" && mode != "work"' '    in_preview && mode != "safe" // knife' \
   a_preview_opens_safe_seats_only
# WP2 any mode opens in a preview
wp '    in_preview && mode != "safe" && mode != "work"' '    in_preview && mode == "dgrs" // knife' \
   a_preview_opens_safe_seats_only
