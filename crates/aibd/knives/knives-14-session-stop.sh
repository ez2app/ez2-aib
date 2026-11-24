#!/usr/bin/env bash
# aib-knives: os=unix
# N-workspace session_stop knives ST1–ST4 (043dd 11-18, 519d0 09:2x「對話框優先」step 4): the seat
# window's stop button is an act a confirmed built-in page may send and a user folder page may not.
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
si() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_input "$3"; }
su() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test session_stop_userpage "$3"; }
# ST1 the act kind is not wired: the page cannot stop a seat
si '        Some(STOP) => return session_stop(st, page, body).await,' \
   '        Some("session_stop_knife") => return session_stop(st, page, body).await, // knife' \
   a_built_in_page_stops_a_seat_and_then_it_takes_nothing
# ST2 the stop is answered but nothing is stopped
si '        Some(_) => s.stop(&id, crate::sessions::DEFAULT_GRACE),' \
   '        Some(_) => s.get(&id).ok_or_else(|| crate::sessions::ApiError::new(aib_core::ErrorCode::SessionNotFound, "x")), // knife' \
   a_built_in_page_stops_a_seat_and_then_it_takes_nothing
# ST4 a page stops a session no seat owns
si '    let seat_session = move || match s.get(&id).and_then(|r| r.seat_id) {' \
   '    let seat_session = move || match s.get(&id).map(|r| r.id) { // knife' \
   a_page_does_not_stop_a_session_no_seat_owns
# ST3 a user folder page stops a seat
su '    let stopper_is_user_page = held_as_user_page(&page).await;' '    let stopper_is_user_page = false && held_as_user_page(&page).await; // knife' \
   a_user_page_does_not_stop_a_seat
