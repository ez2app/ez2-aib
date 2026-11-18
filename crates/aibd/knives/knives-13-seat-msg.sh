#!/usr/bin/env bash
# B1dm-S5 / S3c knives SM1–SM5, RQ1–RQ2 (b4297 11-18, 519d0 07:34 dispatch; DECISION-council-dmq-519d0-1118 §2):
# a seat_send is a library row written queued before anything is typed; a refused send leaves no
# open row; only the receiving seat reads and closes, and a seat gives a reason to close.
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
su() { python3 -I scripts/knife.py crates/aibd/src/seat_send.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "seat_send::tests::$3"; }
se() { python3 -I scripts/knife.py crates/aibd/src/seat_send.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_send "$3"; }
me() { python3 -I scripts/knife.py crates/aibd/src/messages.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_send "$3"; }
# SM1 the row is written delivered before anything was typed
su '        ("status", json!("queued")),' \
   '        ("status", json!("delivered")), // knife' \
   a_seat_send_row_is_queued_first_then_says_how_it_ended
# SM2 a refused send leaves its row open
su '            item.insert("failed".into(), json!(true));' \
   '            item.insert("failed".into(), json!(false)); // knife' \
   a_seat_send_row_is_queued_first_then_says_how_it_ended
# SM3 the row names the receiver as its sender
se '    let (fk, tk) = (format!("seat:{from}"), format!("seat:{to}"));' \
   '    let (fk, tk) = (format!("seat:{to}"), format!("seat:{from}")); // knife' \
   a_seat_reads_and_closes_what_another_seat_sent_it
# SM4 a seat closes without a reason (the page button stands for it)
me '    let fallback = me.starts_with("human:").then_some(PAGE_DONE_WHY);' \
   '    let fallback = Some(PAGE_DONE_WHY); // knife' \
   a_seat_reads_and_closes_what_another_seat_sent_it
# SM5 a seat's acts read another key's messages (the sender's)
me '    let me = format!("seat:{seat_id}");' \
   '    let me = format!("seat:{seat_id}").replace("seat:", "seat:x"); // knife' \
   a_seat_reads_and_closes_what_another_seat_sent_it
# RQ1 a message to a seat waiting for a slot is refused instead of kept queued
se '        "seat_not_running" | "seat_queued" | "seat_in_turn" | "seat_input_busy"' \
   '        "seat_not_running" | "seat_in_turn" | "seat_input_busy" // knife' \
   a_message_to_a_waiting_seat_arrives_when_it_starts
ss2() { python3 -I scripts/knife.py crates/aibd/src/sessions.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test seat_send "$3"; }
# RQ2 a seat that starts is not told about (nothing it was sent while waiting arrives)
ss2 '            let _ = tx.send(seat.to_owned());' \
    '            let _ = (tx, seat); // knife' \
    a_message_to_a_waiting_seat_arrives_when_it_starts

mu() { python3 -I scripts/knife.py crates/aibd/src/messages.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test messages_userpage "$3"; }
# UP1 a user folder page closes what waits on the person (5d33a 2a 預讀 L1)
mu '    if crate::seat_input::held_as_user_page(&page).await {' \
   '    if false && crate::seat_input::held_as_user_page(&page).await { // knife' \
   a_user_page_cannot_close_what_waits_on_the_person
# UP2 a user folder page marks the person's messages read
mu '    if held {' \
   '    if false && held { // knife' \
   a_user_page_cannot_close_what_waits_on_the_person
