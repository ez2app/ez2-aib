#!/bin/bash
# aib-knives: os=unix
# Web seat mailbox G2 knives TH1-TH8 (7ebca 11-20, 5d33a ANSWER-webseat-roundlimit-5d33a-1120,
# 519d0 06:14): 40 per pair, a seat's hour, a cabin's hour that stops it until 繼續.
set -eo pipefail
th() { python3 -I scripts/knife.py crates/aibd/src/seat_send.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
# TH1 three seats taking turns are never caught: the cabin's hour is not counted
th 'or_default()) >= CABIN_PER_HOUR {' 'or_default()) >= CABIN_PER_HOUR * 9 {' \
   a_cabin_past_its_hour_stops_until_a_person_presses_resume
# TH2 a stopped cabin goes on sending once its hour drops
th 'if self.stopped.contains_key(cabin) {' 'if self.stopped.contains_key(cabin) && false {' \
   a_cabin_past_its_hour_stops_until_a_person_presses_resume
# TH3 one seat sends without an hour limit
th 'or_default()) >= SEAT_PER_HOUR {' 'or_default()) >= SEAT_PER_HOUR * 9 {' \
   three_seats_taking_turns_go_past_forty_and_each_stops_at_its_hour
# TH4 a person typing to one seat resets every pair (5d33a answer 6: only that pair)
th 'self.rounds.retain(|(a, b), _| a != seat && b != seat);' 'self.rounds.clear(); let _ = seat;' \
   two_seats_stop_after_twenty_rounds_until_a_person_types
# TH5 a person typing lifts a stopped cabin (only 繼續 does; a timer's text would too)
th 'self.rounds.retain(|(a, b), _| a != seat && b != seat);' \
   'self.rounds.retain(|(a, b), _| a != seat && b != seat); self.stopped.clear();' \
   a_cabin_past_its_hour_stops_until_a_person_presses_resume
# TH6 繼續 keeps the hour: the cabin stops again at once
th 'self.cabin_hour.remove(cabin);' 'let _ = &self.cabin_hour;' \
   a_cabin_past_its_hour_stops_until_a_person_presses_resume
# TH7 a pair counted per direction: two seats answering each other get 80
th '    if a <= b {' '    if true {' two_seats_stop_after_twenty_rounds_until_a_person_types
# TH8 the stopped cabin's card has no 繼續
th 'actions: &["resume"],' 'actions: &[],' a_cabin_past_its_hour_stops_until_a_person_presses_resume
