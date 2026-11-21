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
# BR8 a letter to a 1.x seat sent again with its key is written into the ledger again (0eed7 integ3 B1)
br crates/aibd/src/seat_send.rs \
   '            return ledger_again(&r, &sha);' \
   '            let _ = ledger_again(&r, &sha); // knife' \
   letters_cross_the_bridge_once_each_way \
   '--test seat_bridge letters_cross_the_bridge_once_each_way'
# BR9 the key used for another text answers the first letter
br crates/aibd/src/seat_send.rs \
   '    r.body_sha256 != sha' \
   '    r.body_sha256 != sha && false // knife' \
   letters_cross_the_bridge_once_each_way \
   '--test seat_bridge letters_cross_the_bridge_once_each_way'
# BR10 the key is not kept on the row (a second send writes the ledger twice)
br crates/aibd/src/seat_send.rs \
   '        row.idem_key = Some(k.to_owned());' \
   '        row.idem_key = None; // knife' \
   letters_cross_the_bridge_once_each_way \
   '--test seat_bridge letters_cross_the_bridge_once_each_way'
# BR11 going back leaves the handed-on letters in the web inbox (0eed7 VERIFY-integ3 M2)
br crates/aibd/src/dm_import.rs \
   '    for r in rows.iter().filter(|r| handed_from(r, old_code)) {' \
   '    for r in rows.iter().filter(|r| handed_from(r, old_code) && false) { // knife' \
   going_back_takes_the_handed_on_letters_out_of_the_web_inbox \
   '--lib dm_import::tests::going_back_takes_the_handed_on_letters_out_of_the_web_inbox'
# BR12 going back takes the web seat's own letters too
br crates/aibd/src/dm_import.rs \
   '    s("dm_to") == Some(old_code) && s("dm_id").is_some_and(|i| i.starts_with("q_"))' \
   '    s("dm_to") == Some(old_code) || s("dm_id").is_none() // knife' \
   going_back_takes_the_handed_on_letters_out_of_the_web_inbox \
   '--lib dm_import::tests::going_back_takes_the_handed_on_letters_out_of_the_web_inbox'
# BR13 the ledger reply footer is shown on the web (M3)
br crates/aibd/src/dm_import.rs \
   '        None => without_reply_hint(msg).to_owned(),' \
   '        None => msg.to_owned(), // knife' \
   the_ledger_reply_footer_is_cut_on_the_web \
   '--lib dm_import::tests::the_ledger_reply_footer_is_cut_on_the_web'
# BR14 any letter with the words in it loses its tail at them (M3)
br crates/aibd/src/dm_import.rs \
   "        Some(i) if msg.trim_end().ends_with(')') => msg[..i].trim_end()," \
   "        Some(i) => msg[..i].trim_end(), // knife" \
   the_ledger_reply_footer_is_cut_on_the_web \
   '--lib dm_import::tests::the_ledger_reply_footer_is_cut_on_the_web'
# BR15 going back again finds no seat to tidy (5d33a REVIEW-g5um L1)
br crates/aib-store/src/seat_moves.rs \
   '            "SELECT seat_id FROM seat_moves WHERE old_code = ?1 AND unmoved_at IS NOT NULL' \
   '            "SELECT seat_id FROM seat_moves WHERE old_code = ?1 AND unmoved_at IS NULL' \
   going_back_takes_the_handed_on_letters_out_of_the_web_inbox \
   '--lib dm_import::tests::going_back_takes_the_handed_on_letters_out_of_the_web_inbox'
# BR16 a handed-on letter is not told it is the seat's (519d0 派工 M4)
br crates/aibd/src/dm_import.rs \
   '                let body = format!("{}{note}", row.body.as_deref().unwrap_or_default());' \
   '                let body = row.body.clone().unwrap_or_default(); let _ = &note; // knife' \
   a_handed_on_work_order_says_it_is_the_seats \
   '--lib dm_import::tests::a_handed_on_work_order_says_it_is_the_seats'
# BR17 a work order is told as a plain letter (M4)
br crates/aibd/src/dm_import.rs \
   '    if dispatch {' \
   '    if false && dispatch { // knife' \
   a_handed_on_work_order_says_it_is_the_seats \
   '--lib dm_import::tests::a_handed_on_work_order_says_it_is_the_seats'
