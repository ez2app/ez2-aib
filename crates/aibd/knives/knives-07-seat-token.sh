#!/usr/bin/env bash
# S89-talk-1 knives ST1–ST5 (b4297 11-18, DECISION-council-seat-talk-519d0-1118): a seat token
# lists its own cabin and sends, nothing else; a request cannot set it; it dies with the seat.
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
st() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" \
        -- cargo test -p aibd --locked "$4"; }
# ST1 a seat token reaches every route (pairing, opening seats, sessions)
st crates/aibd/src/guard.rs \
   '        let ok = (path == SEATS && matches!(*req.method(), Method::GET | Method::HEAD))' \
   '        let ok = true || (path == SEATS && matches!(*req.method(), Method::GET | Method::HEAD)) // knife' \
   a_seat_token_only_lists_seats_and_sends_and_dies_with_the_seat
# ST2 a seat sees the seats of every cabin
st crates/aibd/src/seats.rs \
   '        .filter(|v| v["cabin"] == cabin)' \
   '        .filter(|v| v["cabin"] == cabin || v["cabin"] != cabin) // knife' \
   a_seat_sees_only_the_seats_of_its_own_cabin
# ST3 a request may hand a session a seat token
st crates/aibd/src/allowlist.rs \
   '        || env_name_eq(crate::seat_token::ENV, name)' \
   '        || (false && env_name_eq(crate::seat_token::ENV, name)) // knife' \
   a_seat_s_own_env_names_are_never_allowed_from_a_request
# ST4 a replaced token keeps working
st crates/aibd/src/seat_token.rs \
   '            g.by_key.remove(&old);' \
   '            let _ = old; // knife' \
   a_seat_token_names_its_seat_until_replaced_or_revoked
# ST5 a seat's list carries the stopped seats too (519d0 L2)
st crates/aibd/src/seats.rs \
   '            id == me || live.iter().any(|l| l == id)' \
   '            true || id == me || live.iter().any(|l| l == id) // knife' \
   a_seat_sees_only_the_seats_of_its_own_cabin
