#!/usr/bin/env bash
# The web page's timed sentence to a seat (ORDER-webonly-today 7, a9136 11-20)
set -euo pipefail  # a knife that does not bite fails the file (#869 knives 3/3: a MISS here still ended rc 0)
tk() { python3 -I scripts/knife.py crates/aibd/src/seat_tick.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "seat_tick::tests::$3"; }
# KT1 a seat first seen gets its sentence at once (a restart types to every listed seat)
tk '                last.insert(t.seat_id.clone(), now);' '                last.insert(t.seat_id.clone(), now - i64::from(t.every_min) * 60); out.push(t.clone()); // knife' \
   a_sentence_is_due_one_period_after_it_was_first_seen_or_last_sent
# KT2 a seat taken off the list is remembered
tk '    last.retain(|s, _| list.iter().any(|t| &t.seat_id == s));' '    // knife' \
   a_sentence_is_due_one_period_after_it_was_first_seen_or_last_sent
# KT3 a seat in a turn loses its sentence for the period
tk '        InputError::InTurn | InputError::Busy | InputError::Queued | InputError::Starting => {' '        InputError::InTurn => Outcome::Skipped("in_turn"), InputError::Busy | InputError::Queued | InputError::Starting => { // knife' \
   a_seat_that_will_take_it_soon_is_tried_again_and_the_rest_wait_a_period
# KT4 a period under five minutes is taken
tk '    if !(MIN_EVERY..=MAX_EVERY).contains(&t.every_min) {' '    if t.every_min > MAX_EVERY { // knife' \
   an_entry_needs_a_seat_a_period_in_range_and_a_sentence
# KT5 the list has no limit
tk '    if list.len() >= MAX_TICKS {' '    if list.len() >= usize::MAX { // knife' \
   setting_replaces_the_seats_entry_and_the_list_has_a_limit
# KT6 a seat named twice in the file keeps both
tk '        if check(&t).is_ok() && !out.iter().any(|o| o.seat_id == t.seat_id) {' '        if check(&t).is_ok() { // knife' \
   the_file_keeps_good_entries_once_per_seat_and_a_bad_file_is_empty
# KT8 a paused file is read as not paused (急停 does nothing)
tk '    serde_json::from_slice::<List>(bytes).is_ok_and(|l| l.paused)' '    serde_json::from_slice::<List>(bytes).is_ok_and(|_| false) // knife' \
   the_file_keeps_good_entries_once_per_seat_and_a_bad_file_is_empty
# KT9 the page's 急停 is not kept in the file
python3 -I scripts/knife.py crates/aibd/src/seat_tick.rs '        if let Err(e) = save(&list, on) {' '        if let Err(e) = save(&list, false) { // knife' \
   --expect-fail the_page_sets_lists_and_clears_a_seats_timed_sentence \
   -- cargo test -p aibd --locked --test seat_tick the_page_sets_lists_and_clears_a_seats_timed_sentence
# KT7 the act sets a sentence for a seat that is not there
python3 -I scripts/knife.py crates/aibd/src/seat_tick.rs '            if !known {' '            if !known && false { // knife' \
   --expect-fail the_page_sets_lists_and_clears_a_seats_timed_sentence \
   -- cargo test -p aibd --locked --test seat_tick the_page_sets_lists_and_clears_a_seats_timed_sentence
# KT10 a user page that appears after the sentence was set does not stop work / dgrs seats (5d33a PREREAD-seattick L1)
tk '    if user_page && mode != Some("safe") {' '    if user_page && mode == Some("never") { // knife' \
   a_user_page_that_appears_later_stops_all_but_safe_seats
# KT11 a text edited after the fingerprint is sent all the same (DESIGN-drive-in-aibd r2, 5d33a 11-21)
tk '    if changed(t) {' '    if changed(t) && t.seat_id.is_empty() { // knife' \
   a_text_that_is_not_the_approved_one_is_not_sent
# KT12 the approval keeps no hash of the text
tk '        text_sha256: text_sha256(text),' '        text_sha256: String::new(), // knife' \
   a_text_that_is_not_the_approved_one_is_not_sent
# KT13 an approval with no hash kept approves any text
tk '    t.approved && text_sha256(&t.text) != t.text_sha256' '    t.approved && !t.text_sha256.is_empty() && text_sha256(&t.text) != t.text_sha256 // knife' \
   a_text_that_is_not_the_approved_one_is_not_sent
# KT14 the fingerprint does not bind the text
tk '        "text_sha256": sha,' '        "text_sha256": "", // knife' \
   the_fingerprint_binds_the_seat_the_period_and_the_text
# KT15 the fingerprint does not bind the period
tk '        "every_min": t.every_min,' '        "every_min": 0, // knife' \
   the_fingerprint_binds_the_seat_the_period_and_the_text
# KT16 the fingerprint does not bind the seat
tk '        "seat_id": t.seat_id,' '        "seat_id": "", // knife' \
   the_fingerprint_binds_the_seat_the_period_and_the_text
# KT17 letting a paused list go again does not bind the texts
tk '        .map(|t| json!([t.seat_id, t.every_min, text_sha256(&t.text)]))' '        .map(|t| json!([t.seat_id, t.every_min])) // knife' \
   the_fingerprint_binds_the_seat_the_period_and_the_text
# KT18 a sentence is set on a ticket the owner never approved
python3 -I scripts/knife.py crates/aibd/src/seat_tick.rs '            if !crate::owner_ok::approved_for(ticket, &asking) {' '            if ticket.is_empty() && !crate::owner_ok::approved_for(ticket, &asking) { // knife' \
   --expect-fail the_page_sets_lists_and_clears_a_seats_timed_sentence \
   -- cargo test -p aibd --locked --test seat_tick the_page_sets_lists_and_clears_a_seats_timed_sentence
# KT19 a paused list goes again on a ticket the owner never approved
python3 -I scripts/knife.py crates/aibd/src/seat_tick.rs '            if !crate::owner_ok::approved_for(ticket, &ask) {' '            if ticket.is_empty() && !crate::owner_ok::approved_for(ticket, &ask) { // knife' \
   --expect-fail the_page_sets_lists_and_clears_a_seats_timed_sentence \
   -- cargo test -p aibd --locked --test seat_tick the_page_sets_lists_and_clears_a_seats_timed_sentence
# KT20 letting a paused list go again asks no one
python3 -I scripts/knife.py crates/aibd/src/seat_tick.rs '        if paused && !on {' '        if paused && !on && list.len() > 99 { // knife' \
   --expect-fail the_page_sets_lists_and_clears_a_seats_timed_sentence \
   -- cargo test -p aibd --locked --test seat_tick the_page_sets_lists_and_clears_a_seats_timed_sentence
