#!/usr/bin/env bash
# N-ws-dismiss-persist + N-ws-event-pin knives PN1-PN5 (7ebca 11-19, 519d0 07:22): a dismissal
# and a pin are replayed from their events at start; only the built-in page pins, only to an item
# on the tree; the store reads the slot events by type.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
sp() { python3 -I scripts/knife.py crates/aibd/src/slots.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test slots_persist "$3"; }
gp() { python3 -I scripts/knife.py crates/aibd/src/seat_input.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test slots_persist "$3"; }
su() { python3 -I scripts/knife.py crates/aibd/src/slots.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
st() { python3 -I scripts/knife.py crates/aib-store/src/lib.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-store --locked --test store "$3"; }
# PN1 nothing replayed at start
sp 'Ok(taken().replay(&events))' \
   'Ok(taken().replay(&events[..0]))' \
   a_dismissal_and_a_pin_outlive_a_restart
# PN2 a pin is not replayed
sp 'pins().set(card, node.filter(|n| valid_card_id(n)));' \
   'let _ = node;' \
   a_dismissal_and_a_pin_outlive_a_restart
# PN3 any node id is taken
sp 'if !on_tree {' \
   'if false && !on_tree {' \
   a_dismissal_and_a_pin_outlive_a_restart
# PN4 slot_pin skips the page checks
gp 'Some(t @ (crate::slots::ANSWER | crate::slots::DISMISS | crate::slots::RESTORE | crate::slots::PIN | crate::slots::IDEA_ANSWER | crate::slots::REVIEW_SEEN)) => {' \
   'Some(t @ (crate::slots::ANSWER | crate::slots::DISMISS | crate::slots::RESTORE | crate::slots::IDEA_ANSWER | crate::slots::REVIEW_SEEN)) => {' \
   a_dismissal_and_a_pin_outlive_a_restart
# PN5 the store gives every type
st 'FROM (SELECT * FROM events WHERE type IN (SELECT value FROM json_each(?1))' \
   'FROM (SELECT * FROM events WHERE ?1 IS NOT NULL' \
   events_of_some_types_are_the_newest_few_oldest_first
# PN6 a stop seen clean stays a broken card
su 'if stop_clean(payload) {' \
   'if false && stop_clean(payload) {' \
   the_table_puts_each_known_kind_in_its_slot
# PN7 any reason counts as observed clean
su 'payload.get("reason").and_then(Value::as_str) == Some("observed_clean")' \
   'payload.get("reason").is_some()' \
   the_table_puts_each_known_kind_in_its_slot
