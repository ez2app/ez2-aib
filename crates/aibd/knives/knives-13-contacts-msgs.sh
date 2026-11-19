#!/usr/bin/env bash
# ORDER-today-usable knives CM1–CM7 (7b81e 11-18): the contacts list and the flow lines read the message
# library (S89-talk-2a). Counts are what waits for the page and for the seat, a closed decide is not open,
# the newest message dates and sorts the row, flows keep failed apart and stop at 200 pairs.
# Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
cu() { python3 -I scripts/knife.py crates/aibd/src/contacts.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "contacts::tests::$3"; }
cm() { python3 -I scripts/knife.py crates/aibd/src/contacts.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test contacts_messages "$3"; }
# CM1 the seat's own inbox is not counted
cu '        if f.to_key == key && unread {' \
   '        if false && f.to_key == key && unread { // knife' \
   the_counts_are_what_waits_for_the_page_and_for_the_seat
# CM2 a read message still counts as unread
cu '        if to_me && unread {' \
   '        if to_me { // knife' \
   the_counts_are_what_waits_for_the_page_and_for_the_seat
# CM3 the newest message of any pair dates the row
cu '                (f.from_key == key && f.to_key == me) || (f.from_key == me && f.to_key == key)' \
   '                f.from_key == key || f.to_key == key || me.is_empty() // knife' \
   the_counts_are_what_waits_for_the_page_and_for_the_seat
# CM4 a closed (done) decide is still open
cu '        Some("done" | "archived")' \
   '        Some("archived") // knife' \
   the_counts_are_what_waits_for_the_page_and_for_the_seat
# CM5 a failed message is also counted in its kind
cu '        let slot = if f.item.get("failed").and_then(Value::as_bool) == Some(true) {' \
   '        let slot = if f.item.get("failed").and_then(Value::as_bool) == Some(false) { // knife' \
   flows_count_kinds_apart_from_failed_and_carry_no_text
# CM6 flows never stop at 200 pairs
cu '    by.truncate(FLOW_PAIRS_MAX);' \
   '    let _ = FLOW_PAIRS_MAX; // knife' \
   flows_count_kinds_apart_from_failed_and_carry_no_text
# CM7 the newest message does not sort the list
cm '                    .cmp(&a["last_message_at"].as_str())' \
   '                    .cmp(&b["last_message_at"].as_str()) // knife' \
   the_contacts_list_and_flows_count_the_library
