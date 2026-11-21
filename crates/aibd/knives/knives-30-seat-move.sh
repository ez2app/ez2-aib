#!/usr/bin/env bash
# G5 信橋 seat_move (DESIGN-g5-bridge-7ebca-1121 r1 §1; 5d33a PREREAD-g5-bridge M1)
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
sv() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" -- cargo test -p aibd --locked $5; }
# SV1 an old code with no folder in the 1.x ledger is moved
sv crates/aibd/src/seat_move.rs \
   '    if !f.in_ledger {' \
   '    if false && !f.in_ledger { // knife' \
   a_seat_is_moved_by_the_owners_finger_only_and_moved_back_by_the_page \
   '--test seat_move a_seat_is_moved_by_the_owners_finger_only_and_moved_back_by_the_page'
# SV2 a ticket for another move writes this one
sv crates/aibd/src/seat_move.rs \
   '    if ask_of(old_code, seat_id, String::new()).params != approved {' \
   '    if false && ask_of(old_code, seat_id, String::new()).params != approved { // knife' \
   the_ticket_is_for_this_move_only \
   '--lib seat_move::tests::the_ticket_is_for_this_move_only'
# SV3 the move asks no window (seat.move missing from WIRED)
sv crates/aibd/src/owner_ok.rs \
   '    crate::seat_move::OP,  // seat.move (G5 信橋, 5d33a PREREAD-g5-bridge M1)' \
   '    // knife: seat.move not wired' \
   every_operation_constant_is_wired \
   '--lib owner_ok::tests::every_operation_constant_is_wired'
