#!/usr/bin/env bash
# ST1–ST17 T1: the owner's fingerprint makes a seat trusted (DESIGN-trust-t1-7b81e-1120; 5d33a PREREAD-trust-t1 L1 L2): one knife a line of defence
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
st() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "seat_trust::tests::$4" \
        -- cargo test -p aibd --locked --lib "seat_trust::tests::$4"; }
tw() { python3 -I scripts/knife.py crates/aibd/src/trusted_seats.rs "$1" "$2" --expect-fail "trusted_seats::tests::$3" \
        -- cargo test -p aibd --locked --lib "trusted_seats::tests::$3"; }
si() { python3 -I scripts/knife.py crates/aibd/src/seat_trust.rs "$1" "$2" \
  --expect-fail a_trust_is_given_by_the_owners_finger_only_and_taken_back_by_the_page \
  -- cargo test -p aibd --locked --test seat_trust a_trust_is_given_by_the_owners_finger_only_and_taken_back_by_the_page; }
# ST1 asked for with the switch off
st crates/aibd/src/seat_trust.rs \
   '    if !f.switch_on {' \
   '    if !f.switch_on && f.user_page {' \
   only_a_live_safe_or_work_seat_is_asked_for_and_only_with_the_switch
# ST2 asked for under a user folder page
st crates/aibd/src/seat_trust.rs \
   '    if f.user_page {' \
   '    if f.user_page && !f.switch_on {' \
   only_a_live_safe_or_work_seat_is_asked_for_and_only_with_the_switch
# ST3 an archived seat is asked for
st crates/aibd/src/seat_trust.rs \
   '        Some("ARCHIVED") => return Err((StatusCode::NOT_FOUND, "seat_archived")),' \
   '        Some("GONE") => return Err((StatusCode::NOT_FOUND, "seat_archived")),' \
   only_a_live_safe_or_work_seat_is_asked_for_and_only_with_the_switch
# ST4 a dgrs seat is asked for
st crates/aibd/src/seat_trust.rs \
   '    if !matches!(f.mode.as_deref(), Some("safe" | "work")) {' \
   '    if !matches!(f.mode.as_deref(), Some("safe" | "work" | "dgrs")) {' \
   only_a_live_safe_or_work_seat_is_asked_for_and_only_with_the_switch
# ST5 the gate is not passed again before the write (5d33a L1)
st crates/aibd/src/seat_trust.rs \
   '    gate(f)?;' \
   '    let _ = gate(f); // knife' \
   what_changed_after_the_fingerprint_stops_the_write
# ST6 what changed after the fingerprint is written anyway (5d33a L2)
st crates/aibd/src/seat_trust.rs \
   '    if ask_of(seat_id, f, String::new()).params != approved {' \
   '    if ask_of(seat_id, f, String::new()).params.is_empty() && approved.is_empty() {' \
   what_changed_after_the_fingerprint_stops_the_write
# ST7 the ticket does not bind the remote
st crates/aibd/src/seat_trust.rs \
   '        "remote": f.remote,' \
   '        "remote": "",' \
   the_ticket_binds_the_seat_its_mode_its_remote_and_the_machine_list
# ST8 the ticket does not bind the machine list
st crates/aibd/src/seat_trust.rs \
   '        "hosts_digest": f.hosts_digest,' \
   '        "hosts_digest": "",' \
   the_ticket_binds_the_seat_its_mode_its_remote_and_the_machine_list
# ST9 the ticket does not bind the mode
st crates/aibd/src/seat_trust.rs \
   '        "mode": f.mode,' \
   '        "mode": "",' \
   the_ticket_binds_the_seat_its_mode_its_remote_and_the_machine_list
# ST10 the ticket does not bind the seat
st crates/aibd/src/seat_trust.rs \
   '        "seat_id": seat_id,' \
   '        "seat_id": "",' \
   the_ticket_binds_the_seat_its_mode_its_remote_and_the_machine_list
# ST11 a long remote pushes the seat's code out of the window
st crates/aibd/src/seat_trust.rs \
   '            .take(24)' \
   '            .take(300)' \
   the_window_is_aibds_words_with_the_key_facts_first
# ST12 the trust lasts longer than 12 hours
st crates/aibd/src/trusted_seats.rs \
   'pub const HOURS: u64 = 12;' \
   'pub const HOURS: u64 = 48;' \
   the_window_is_aibds_words_with_the_key_facts_first
# ST13 a seat trusted now is asked again
si '        .filter(|u| crate::trusted_seats::holds(Some(*u), now))' \
   '        .filter(|u| crate::trusted_seats::holds(Some(*u), now) && *u == 0)'
# ST14 a ticket that was never approved writes the trust
si '    if !crate::owner_ok::approved_for(&ticket, &ask) {' \
   '    if ticket.is_empty() && !crate::owner_ok::approved_for(&ticket, &ask) {'
# ST15 taking a trust back takes nothing
si '        crate::trusted_seats::take(&data, &sid, crate::pair_carry::now_unix())' \
   '        crate::trusted_seats::take(&data, &sid.replace("seat_", "seat_x"), crate::pair_carry::now_unix())'
# ST16 the trust is written through a link
tw '    if is_link(&dir) || is_link(&file) {' \
   '    if is_link(&dir) && is_link(&file) {' \
   a_trust_is_never_written_through_a_link
# ST17 an ended trust stays in the file
tw '                .filter(|(_, u)| *u > now)' \
   '                .filter(|(_, u)| *u > now.min(0))' \
   a_trust_is_written_whole_and_taken_back
