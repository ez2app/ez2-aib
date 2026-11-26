#!/bin/bash
# aib-knives: os=unix
# 519d0 VERIFY-selftest-519d0-2a3e3cbc r2 L5 knives KD1-KD4 (7ebca 11-20): the person_vs_person
# ask (two of the person's own requests disagree) keeps the posts' written order, takes only
# the person's posts, and is answered only by its own two fixed answers.
set -eo pipefail
kd() { python3 -I scripts/knife.py crates/aibd/src/asks.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test asks "$3"; }
# KD1 the seat's order is kept, not the order the posts were written
kd 'if (a.created_unix, &a.message_id) <= (b.created_unix, &b.message_id) {' \
   'if true { // knife' a_seat_asks_which_of_two_requests_and_the_card_keeps_their_order
# KD2 the review kind's answers answer a person_vs_person card
kd '    if !fits(kind, answer) {' \
   '    if !fits(kind, answer) && false { // knife' a_seat_asks_which_of_two_requests_and_the_card_keeps_their_order
# KD3 the new kind is refused
kd '        Some(KIND_PERSONS) => true,' \
   '        Some("knife") => true,' a_seat_asks_which_of_two_requests_and_the_card_keeps_their_order
# KD4 a seat's post is taken as one of the person's two
kd 'a.from_key.starts_with("human:") && b.from_key.starts_with("human:")' \
   'a.from_key.starts_with("human:")' a_seat_asks_which_of_two_requests_and_the_card_keeps_their_order
