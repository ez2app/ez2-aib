#!/usr/bin/env bash
# 網頁版待審框第 1 期 aibd 信線 (DESIGN-web-inbox-aibd-7ebca-1121 r1; DECISION-council-webinbox §4 §5)
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
wk() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" -- cargo test -p aibd --locked $5; }
# WK1 a web seat a bridged letter was put in is not woken
wk crates/aibd/src/dm_import.rs \
   '                    woken(&seat_id);' \
   '                    let _ = (&seat_id, woken); // knife' \
   a_letter_to_a_moved_code_lands_once_in_the_web_seats_inbox \
   '--lib dm_import::tests::a_letter_to_a_moved_code_lands_once_in_the_web_seats_inbox'
# WK2 a move leaves a letter the mirror never posted in the old ledger
wk crates/aibd/src/dm_import.rs \
   '                if let Inserted::New = db.insert(row).map_err(|e| e.to_string())? {' \
   '                if let Inserted::Existing(_) = db.insert(row).map_err(|e| e.to_string())? { // knife' \
   a_move_brings_the_open_letters_along_once \
   '--lib dm_import::tests::a_move_brings_the_open_letters_along_once'
# WK3 a letter is handed on again (no loop guard)
wk crates/aibd/src/dm_import.rs \
   '                if had.item.get("forwarded_to").is_some_and(|v| !v.is_null()) || had.to_key == to {' \
   '                if false { // knife' \
   a_move_brings_the_open_letters_along_once \
   '--lib dm_import::tests::a_move_brings_the_open_letters_along_once'
# WK4 the ledger copy is closed without saying it came through the bridge
wk crates/aibd/src/dm_import.rs \
   '    let why = format!("{CLOSED_ON_WEB} {}(經信橋)", row.message_id);' \
   '    let why = format!("{CLOSED_ON_WEB}"); // knife' \
   closing_on_the_web_closes_the_ledger_copy \
   '--lib dm_import::tests::closing_on_the_web_closes_the_ledger_copy'
# WK5 the ledger copy is closed as a new actor kind (5d33a PREREAD-inboxaibd L1)
wk crates/aibd/src/dm_import.rs \
   '        &format!("seat:{code}"),' \
   '        "aibd_bridge", // knife' \
   closing_on_the_web_closes_the_ledger_copy \
   '--lib dm_import::tests::closing_on_the_web_closes_the_ledger_copy'
# WK6 a file under another name, or another code's mirrored row, is handed on (5d33a REVIEW-webinbox1 L1)
wk crates/aibd/src/dm_import.rs \
   '        if !named {' \
   '        if false && !named { // knife' \
   a_move_brings_the_open_letters_along_once \
   '--lib dm_import::tests::a_move_brings_the_open_letters_along_once'
wk crates/aibd/src/dm_import.rs \
   '                if !to_this_code {' \
   '                if false && !to_this_code { // knife' \
   a_move_brings_the_open_letters_along_once \
   '--lib dm_import::tests::a_move_brings_the_open_letters_along_once'
# WK7 a seat's sent box shows another seat's letters (r1 E)
wk crates/aib-store/src/seat_moves.rs \
   '            "SELECT {} FROM messages WHERE from_key = ?1' \
   '            "SELECT {} FROM messages WHERE ?1 IS NOT NULL -- knife' \
   letters_cross_the_bridge_once_each_way \
   '--test seat_bridge letters_cross_the_bridge_once_each_way'
