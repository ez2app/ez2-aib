#!/usr/bin/env bash
# aib-knives: os=unix
# N-workspace knives CY2 (CY1 retired: b4297 eb0bf164c holds the assignment half, knife PO11) (7b81e 11-18; 519d0 r16 ruling 3, MATRIX P2 r4, 043dd 12:49): a pairing carried
# over an update neither changes assignments nor writes the tree. Each rule, broken, turns its own test red.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
cy() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test pair_carry "$3"; }
# CY2 a carried pairing writes the tree
cy '            let carried_pair = page.carried;' \
   '            let carried_pair = false && page.carried; // knife' \
   an_update_carries_the_page_once_and_for_safe_seats_only
