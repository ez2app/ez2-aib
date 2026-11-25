#!/usr/bin/env bash
# aib-knives: os=unix
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
tk '    if changed(t, body) {' '    if changed(t, body) && t.seat_id.is_empty() { // knife' \
   a_text_that_is_not_the_approved_one_is_not_sent
# KT12 the approval keeps no hash of the text
tk '        text_sha256: text_sha256(text),' '        text_sha256: String::new(), // knife' \
   a_text_that_is_not_the_approved_one_is_not_sent
# KT13 an approval with no hash kept approves any text
tk '    text_sha256(body) != t.text_sha256' '    !t.text_sha256.is_empty() && text_sha256(body) != t.text_sha256 // knife' \
   a_text_that_is_not_the_approved_one_is_not_sent
# KT14 the fingerprint does not bind the text
tk '        text_sha256: &t.text_sha256,' '        text_sha256: "", // knife' \
   the_fingerprint_binds_the_seat_the_period_and_the_text
# KT15 the fingerprint does not bind the period
tk '        every_min: t.every_min,' '        every_min: 0, // knife' \
   the_fingerprint_binds_the_seat_the_period_and_the_text
# KT16 the fingerprint does not bind the seat
tk '        seat_id: &t.seat_id,' '        seat_id: "", // knife' \
   the_fingerprint_binds_the_seat_the_period_and_the_text
# KT17 letting a paused list go again does not bind the texts
tk '        .map(|t| json!([t.seat_id, t.every_min, t.file, entry_sha(t)]))' '        .map(|t| json!([t.seat_id, t.every_min, entry_sha(t).len()])) // knife' \
   the_fingerprint_binds_the_seat_the_period_and_the_text
# KT18 a sentence is set on a ticket the owner never approved
python3 -I scripts/knife.py crates/aibd/src/seat_tick.rs '            let Some(sig) = sig else {' '            let Some(sig) = sig.or(Some(Vec::new())) else { // knife' \
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
# KT21 a file's approval keeps no hash of its content (DESIGN-drive-in-aibd r2 §1)
tk '        text_sha256: text_sha256(body),' '        text_sha256: text_sha256(&body[..0]), // knife' \
   a_file_that_is_not_the_approved_content_is_not_sent
# KT23 a file that cannot be read is sent as nothing went wrong
tk '        Err(why) => return Some(*why),' '        Err(_) => return None, // knife' \
   a_file_that_is_not_the_approved_content_is_not_sent
# KT24 the fingerprint does not bind the file's name
tk '        file: &t.file,' '        file: "", // knife' \
   a_file_that_is_not_the_approved_content_is_not_sent
# KT25 a link in the ticks folder is followed
tk '    if !m.is_file() {' '    if m.is_dir() { // knife' \
   a_file_is_a_name_under_the_ticks_folder_read_whole_and_clean
# KT26 a path is taken as a file's name
tk '    if !file_name_ok(name) {' '    if name.is_empty() { // knife' \
   a_file_is_a_name_under_the_ticks_folder_read_whole_and_clean
# KT27 a file of any size is read
tk '    if u64::try_from(bytes.len()).unwrap_or(u64::MAX) > MAX_TICK_FILE {' '    if bytes.is_empty() && bytes.len() > 1 { // knife' \
   a_file_is_a_name_under_the_ticks_folder_read_whole_and_clean
# KT28 a file's words are typed as they are (escape sequences and hidden characters stay)
tk '    let text = crate::dm_import::clean(&text);' '    // knife' \
   a_file_is_a_name_under_the_ticks_folder_read_whole_and_clean
# KT29 an entry takes a text and a file at once
tk '        if !t.text.is_empty() {' '        if !t.text.is_empty() && t.every_min == 0 { // knife' \
   a_file_is_a_name_under_the_ticks_folder_read_whole_and_clean
# KT30 rounds that do not go in are never told
tk '                .is_multiple_of(STUCK_EVERY)' '                .is_multiple_of(u32::MAX) // knife' \
   three_rounds_in_a_row_that_do_not_go_in_are_told_and_then_every_three
# KT31 a round that goes in does not start the count again
tk '                ..Seen::default()' '                ..seen.clone() // knife' \
   three_rounds_in_a_row_that_do_not_go_in_are_told_and_then_every_three
# KT32 a seat that stays in a turn is never told
tk '            let tell = late > seen.late && late.is_multiple_of(STUCK_EVERY);' '            let tell = late > seen.late && late.is_multiple_of(u32::MAX); // knife' \
   three_rounds_in_a_row_that_do_not_go_in_are_told_and_then_every_three
# KT34 an entry with no signature is sent (DESIGN-drive-in-aibd r3, 5d33a 11-21: old entries too)
tk '    if t.sig.is_empty() {' '    if t.sig.is_empty() && t.seat_id.is_empty() { // knife' \
   only_an_entry_the_owners_key_signed_is_sent
# KT35 the signature is not checked: a list rewritten whole is sent
tk '    if !signed(t, c) {' '    if !signed(t, c) && t.seat_id.is_empty() { // knife' \
   only_an_entry_the_owners_key_signed_is_sent
# KT36 a cleared approval put back is sent
tk '    if c.revoked.iter().any(|r| r == &t.id) {' '    if c.revoked.iter().any(|r| r == &t.id) && t.seat_id.is_empty() { // knife' \
   only_an_entry_the_owners_key_signed_is_sent
# KT37 an approval never ends
tk '    if c.now >= until_of(t) {' '    if c.now >= u64::MAX { // knife' \
   only_an_entry_the_owners_key_signed_is_sent
# KT38 the signature does not bind the approval's own id
tk '        id: &t.id,' '        id: "", // knife' \
   only_an_entry_the_owners_key_signed_is_sent
# KT39 the signature does not bind when it was given
tk '        at: t.approved_at,' '        at: 0, // knife' \
   only_an_entry_the_owners_key_signed_is_sent
# KT40 the owner signature for another act fits (the operation is not the constant of this module)
tk '        operation: OP_SET,' '        operation: if t.sig.is_empty() { OP_SET } else { OP_RESUME }, // knife' \
   only_an_entry_the_owners_key_signed_is_sent
# KT41 a signature by any key is taken, not only the one checked at start
python3 -I scripts/knife.py crates/aibd/src/touch.rs '        let by_the_checked_key = self.dev.verify(&self.key.public, msg, sig);' '        let by_the_checked_key = !msg.is_empty(); // knife' \
   --expect-fail seat_tick::tests::only_an_entry_the_owners_key_signed_is_sent \
   -- cargo test -p aibd --locked --lib seat_tick::tests::only_an_entry_the_owners_key_signed_is_sent
# KT42 the revoked list forgets the newest id when it is full
tk '    let over = have.len().saturating_sub(MAX_REVOKED);' '    let over = 0; have.truncate(MAX_REVOKED); // knife' \
   only_an_entry_the_owners_key_signed_is_sent
