#!/usr/bin/env bash
# aib-knives: os=unix
# which web seat a 1.x seat moved into (G5 信橋 DESIGN-g5-bridge-7ebca-1121 r1 §1; 5d33a PREREAD-g5-bridge M1)
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
mv_() { python3 -I scripts/knife.py crates/aib-store/src/seat_moves.rs "$1" "$2" --expect-fail "$3" \
          -- cargo test -p aib-store --locked --lib "seat_moves::tests::$3"; }
# MV1 a move that went back still answers
mv_ '        "SELECT seat_id FROM seat_moves WHERE old_code = ?1 AND unmoved_at IS NULL",' \
    '        "SELECT seat_id FROM seat_moves WHERE old_code = ?1", // knife' \
    a_move_is_one_live_row_each_way_and_going_back_keeps_it
# MV2 any five letters are taken as a seat code
mv_ '            .all(|b| b.is_ascii_digit() || (b'"'"'a'"'"'..=b'"'"'f'"'"').contains(&b))' \
    '            .all(|_| true) // knife' \
    only_a_five_hex_code_is_taken
