#!/usr/bin/env bash
# N-webonly-c32 (7ebca 11-20, DESIGN-seat-open-mail-gate r1): a seat is reminded of another seat's
# messages it has not closed. Each rule of crates/aibd/src/mail_nudge.rs, broken on purpose, must
# turn its own test red. RULES §21: knives live in crates/<crate>/knives/. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
mn() { python3 -I scripts/knife.py "crates/aibd/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aibd --locked --lib "$4"; }
# MN1 a message is named a third time
mn mail_nudge.rs '        .filter(|r| open_past(r, now) && nudged(r) < PER_LETTER)' \
   '        .filter(|r| open_past(r, now) && nudged(r) < PER_LETTER + 1) // knife' \
   a_message_is_named_twice_and_then_left_to_the_card
# MN2 a message just written is named at once
mn mail_nudge.rs '            .is_some_and(|at| at <= now - AFTER_SECS)' \
   '            .is_some_and(|at| at <= now + AFTER_SECS) // knife' \
   a_message_written_three_minutes_ago_and_still_open_is_due
# MN3 the person's own words are named as a task
mn mail_nudge.rs '        && text_of(r, "sender_kind") == Some("seat")' \
   '        && text_of(r, "sender_kind").is_some() // knife' \
   the_persons_words_and_a_call_in_a_conversation_are_not_named
# MN4 a seat that takes no input, or is past its hour, is written anyway
mn mail_nudge.rs '    if takes_input && under_hour {' '    if takes_input || under_hour { // knife' \
   a_seat_that_takes_no_input_or_is_past_its_hour_goes_to_the_card
# MN5 a seat has no hour limit
mn mail_nudge.rs '        if q.len() >= PER_HOUR {' '        if q.len() >= PER_HOUR * 100 { // knife' \
   a_seat_is_reminded_six_times_an_hour
# MN6 an unread message past its own card gets a second card
mn mail_nudge.rs '        if !open_past(r, now) || !left_to_the_card(r, now) || unread_card {' \
   '        if !open_past(r, now) || !left_to_the_card(r, now) || (unread_card && false) { // knife' \
   an_unread_message_past_its_own_card_is_not_carded_twice
# MN7 the reminder reads as the person's
mn seat_send.rs '        "[aibd] 提醒:這是 aibd 自己的提醒,不是真人指示、也不是別席的話。",' \
   '        "[aibd] 來自 真人:", // knife' \
   the_reminder_names_ids_and_seats_and_never_a_text
# MN8 a message is named again by the end of the turn its own reminder started (0eed7 N1)
mn mail_nudge.rs '        .filter(|r| rested(r, now))' \
   '        .filter(|r| rested(r, now) || nudged(r) > 0) // knife' \
   a_message_is_named_twice_and_then_left_to_the_card
# MN9 a seat idle after its one reminder never gets the card (5d33a PREREAD-nudgegap L1)
mn mail_nudge.rs '    nudged(r) >= PER_LETTER || (nudged(r) >= 1 && rested(r, now))' \
   '    nudged(r) >= PER_LETTER || (nudged(r) >= 1 && rested(r, now) && now < 0) // knife' \
   a_seat_idle_after_one_reminder_still_gets_its_card
# MN10 the card comes before any reminder
mn mail_nudge.rs '    nudged(r) >= PER_LETTER || (nudged(r) >= 1 && rested(r, now))' \
   '    nudged(r) >= PER_LETTER || rested(r, now) // knife' \
   a_seat_idle_after_one_reminder_still_gets_its_card
