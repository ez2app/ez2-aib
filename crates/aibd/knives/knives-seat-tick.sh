#!/usr/bin/env bash
# The web page's timed sentence to a seat (ORDER-webonly-today 7, a9136 11-20)
set -u
tk() { python3 -I scripts/knife.py crates/aibd/src/seat_tick.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "seat_tick::tests::$3"; }
# KT1 a seat first seen gets its sentence at once (a restart types to every listed seat)
tk '                last.insert(t.seat_id.clone(), now);' '                last.insert(t.seat_id.clone(), now - i64::from(t.every_min) * 60); out.push(t.clone()); // knife' \
   a_sentence_is_due_one_period_after_it_was_first_seen_or_last_sent
# KT2 a seat taken off the list is remembered
tk '    last.retain(|s, _| list.iter().any(|t| &t.seat_id == s));' '    // knife' \
   a_sentence_is_due_one_period_after_it_was_first_seen_or_last_sent
# KT3 a seat in a turn loses its sentence for the period
tk '        InputError::InTurn | InputError::Busy | InputError::Queued | InputError::Starting => {' '        InputError::Busy | InputError::Queued | InputError::Starting => {' \
   a_seat_that_will_take_it_soon_is_tried_again_and_the_rest_wait_a_period
# KT4 a period under five minutes is taken
tk '    if !(MIN_EVERY..=MAX_EVERY).contains(&t.every_min) {' '    if t.every_min > MAX_EVERY { // knife' \
   an_entry_needs_a_seat_a_period_in_range_and_a_sentence
# KT5 the list has no limit
tk '        None if list.len() >= MAX_TICKS => return Err("too_many_ticks"),' '        None if list.len() >= usize::MAX => return Err("too_many_ticks"), // knife' \
   setting_replaces_the_seats_entry_and_the_list_has_a_limit
# KT6 a seat named twice in the file keeps both
tk '        if check(&t).is_ok() && !out.iter().any(|o| o.seat_id == t.seat_id) {' '        if check(&t).is_ok() { // knife' \
   the_file_keeps_good_entries_once_per_seat_and_a_bad_file_is_empty
# KT7 the act sets a sentence for a seat that is not there
python3 -I scripts/knife.py crates/aibd/src/seat_tick.rs '            if !known {' '            if !known && false { // knife' \
   --expect-fail the_page_sets_lists_and_clears_a_seats_timed_sentence \
   -- cargo test -p aibd --locked --test seat_tick the_page_sets_lists_and_clears_a_seats_timed_sentence
