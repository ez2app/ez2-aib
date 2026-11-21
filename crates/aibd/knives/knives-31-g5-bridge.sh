#!/usr/bin/env bash
# G5 信橋 both ways (DESIGN-g5-bridge-7ebca-1121 r1 §2 §3; 5d33a PREREAD-g5-bridge Q2 Q4 N1)
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
br() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" -- cargo test -p aibd --locked $5; }
# BR1 a letter to a moved code is not routed to the web seat
br crates/aibd/src/dm_import.rs \
   '        if let Some(seat_id) = moved_into {' \
   '        if let Some(seat_id) = moved_into.filter(|_| false) { // knife' \
   a_letter_to_a_moved_code_lands_once_in_the_web_seats_inbox \
   '--lib dm_import::tests::a_letter_to_a_moved_code_lands_once_in_the_web_seats_inbox'
# BR2 a letter that only claims an aibd id is taken as the web seat's own
br crates/aibd/src/dm_import.rs \
   '        .filter(|r| r.to_key == want))' \
   '        .filter(|r| !want.is_empty() || r.to_key == want)) // knife' \
   a_web_seats_own_letter_follows_its_row_and_a_claimed_id_does_not \
   '--lib dm_import::tests::a_web_seats_own_letter_follows_its_row_and_a_claimed_id_does_not'
# BR3 an e-mail's name part reaches the web seat
br crates/aibd/src/dm_import.rs \
   '                out.push_str("***@");' \
   '                out.push_str(&word[..word.len() - host.len()]); // knife' \
   only_the_name_of_an_email_is_masked \
   '--lib dm_import::tests::only_the_name_of_an_email_is_masked'
# BR4 the fixed line that says it is data is dropped
br crates/aibd/src/dm_import.rs \
   '    let mut out = format!("〔{BRIDGE_DATA_LINE}〕\n〔寄件:{label}〕\n{body}");' \
   '    let mut out = format!("〔寄件:{label}〕\n{body}"); // knife' \
   a_letter_to_a_moved_code_lands_once_in_the_web_seats_inbox \
   '--lib dm_import::tests::a_letter_to_a_moved_code_lands_once_in_the_web_seats_inbox'
# BR5 a web seat's letter names its own web code as a 1.x seat
br crates/aibd/src/legacy_send.rs \
   '                ("sender_seat", json!(old_code)),' \
   '                ("sender_seat", json!(old_code.or(Some(web_code)))), // knife' \
   a_web_seats_letter_names_only_its_moved_code \
   '--lib legacy_send::tests::a_web_seats_letter_names_only_its_moved_code'
# BR6 a web seat's letter to the ledger does not go through seat_send end to end
br crates/aibd/src/seat_send.rs \
   '            if moved_to.is_none()' \
   '            if false && moved_to.is_none() // knife' \
   letters_cross_the_bridge_once_each_way \
   '--test seat_bridge letters_cross_the_bridge_once_each_way'
# BR7 a seat not work / dgrs writes into a 1.x seat's ledger (5d33a REVIEW-g5bridge M1, L1)
br crates/aibd/src/seat_send.rs \
   '    matches!(mode, Some("work" | "dgrs"))' \
   '    !matches!(mode, Some("safe")) // knife' \
   only_work_and_dgrs_seats_write_into_a_ledger \
   '--lib seat_send::tests::only_work_and_dgrs_seats_write_into_a_ledger'
