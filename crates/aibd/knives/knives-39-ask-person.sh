#!/bin/bash
# 「要你決定」卡 knives AP1-AP7 (7ebca 11-19, 519d0 20:22 (5), PLAN-ask-person-card-7ebca-1119):
# a seat asks with ids only, aibd checks them, one open ask a seat, a pace, the answer is one of
# two fixed values, an answered ask leaves the board.
set -eo pipefail
ap() { python3 -I scripts/knife.py crates/aibd/src/asks.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test asks "$3"; }
au() { python3 -I scripts/knife.py crates/aibd/src/asks.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "asks::tests::$3"; }
# AP1 the seat's own post (or any seat's) counts as the review
ap '        Some(m) if m.from_key.starts_with("seat:") && m.from_key != me => {}' \
   '        Some(m) if m.from_key.starts_with("seat:") => {}' a_seat_asks_with_ids_and_only_the_page_answers_with_one_of_two
# AP2 any post counts as the person's request
ap '        Some(m) if m.from_key.starts_with("human:") => {}' \
   '        Some(_) => {}' a_seat_asks_with_ids_and_only_the_page_answers_with_one_of_two
# AP3 a seat not in the thread asks
ap '    if !member {' \
   '    if !member && false {' a_seat_asks_with_ids_and_only_the_page_answers_with_one_of_two
# AP4 a second ask while one is open
au '    if let Some(open) = open_asks(events).into_iter().find(mine) {' \
   '    if let Some(open) = open_asks(events).into_iter().find(mine).filter(|_| false) {' \
   one_open_ask_a_seat_and_the_same_ask_is_the_same
# AP5 any words are an answer
ap '    if !fits(kind, answer) {' \
   '    if !fits(kind, answer) && false {' a_seat_asks_with_ids_and_only_the_page_answers_with_one_of_two
# AP6 an answered ask keeps its card
au '        .filter(|e| text(&e.payload, "ask_id").is_some_and(|a| !done.contains(a)))' \
   '        .filter(|e| text(&e.payload, "ask_id").is_some_and(|a| done.is_empty() || !a.is_empty()))' \
   an_answered_ask_has_no_card_and_three_in_ten_minutes_is_the_most
# AP7 no pace
au '    if recent >= PER_WINDOW {' \
   '    if recent >= PER_WINDOW + 100 {' \
   an_answered_ask_has_no_card_and_three_in_ten_minutes_is_the_most
