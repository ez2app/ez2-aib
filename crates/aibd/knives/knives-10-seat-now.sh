#!/usr/bin/env bash
# N-now slice 1 knives NW1–NW7 (7b81e 11-18, 519d0 05:24 ruling): a seat writes only its own
# short one-line status, never names another seat, never reads the board, and the journal keeps
# lengths and hashes, never the words. Each rule, broken, turns its own test red.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
nu() { python3 -I scripts/knife.py crates/aibd/src/seat_now.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "seat_now::tests::$3"; }
ne() { python3 -I scripts/knife.py crates/aibd/src/seat_now.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_now "$3"; }
ni() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_now "$3"; }
ng() { python3 -I scripts/knife.py crates/aibd/src/guard.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_now "$3"; }
# NW1 an unknown field (a seat id) is taken
nu '            return Err(Refused("unknown_field"));' \
   '            continue; // knife' \
   a_line_is_short_one_line_and_has_no_seat_field
# NW2 a second line gets in
nu '        Some(Value::String(s)) if s.chars().count() <= max && !s.chars().any(char::is_control) => {' \
   '        Some(Value::String(s)) if s.chars().count() <= max => { // knife' \
   a_line_is_short_one_line_and_has_no_seat_field
# NW3 no limit on the numbers
nu '            .filter(|n| *n <= max)' \
   '            .filter(|n| *n <= max || true) // knife (max stays used: -D warnings)' \
   a_line_is_short_one_line_and_has_no_seat_field
# NW4 the journal keeps the words
nu '            "item": text(Some(&n.item)),' \
   '            "item": n.item, // knife' \
   the_journal_has_numbers_and_hashes_never_the_words
# NW5 clear keeps the line
nu '                g.remove(seat_id);' \
   '                let _ = seat_id; // knife' \
   the_board_keeps_one_line_per_seat_and_clear_removes_it
# NW6 a seat's seat_now goes to seat_send (no dispatch)
ni '            if body.get("type").and_then(Value::as_str) == Some(crate::seat_now::KIND) {' \
   '            if false && body.get("type").and_then(Value::as_str) == Some(crate::seat_now::KIND) { // knife' \
   a_seat_writes_only_its_own_line_and_only_a_page_reads_the_board
# NW7 a seat token reads the board
ng '            || (path == ACT && *req.method() == Method::POST);' \
   '            || (path == ACT && *req.method() == Method::POST) || path == NOW; // knife' \
   a_seat_writes_only_its_own_line_and_only_a_page_reads_the_board
