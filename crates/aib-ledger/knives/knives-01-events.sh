#!/usr/bin/env bash
# S107-product slice 1 (a9136 11-17; IFACE-s107-product-a9136-1117 r1 §1 §8): action events. Each rule,
# broken, turns its own test red. Run on macOS (the header default) by the shared crate-knives step
# (crates/aib-knife/run-knives.sh rust, main 454ccd67 #836); no step of its own. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
ek() { python3 -I scripts/knife.py "crates/aib-ledger/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-ledger --locked "$4"; }
# EV1 a kind's own field rewrites who / which card
ek events.rs '        if !COMMON.contains(&k.as_str()) {' '        if !COMMON.contains(&k.as_str()) || k.len() < 100 { // knife' \
   a_kind_cannot_rewrite_the_common_fields
# EV2 a host or knife name in the status line is taken as a card
ek events.rs '            && is_node(w)' '            && (is_node(w) || w.len() > 1) // knife' \
   only_card_ids_in_the_tree_are_taken_from_a_status_line
# EV3 two events in one process share an id
ek events.rs '    let seq = SEQ.fetch_add(1, Ordering::Relaxed);' '    let seq = SEQ.load(Ordering::Relaxed); // knife' \
   ids_never_repeat_and_a_nameless_machine_gets_a_random_tag
# EV4 a failed write stops the caller
ek events.rs '        eprintln!("{prog}: event not written: {e}");' '        panic!("{prog}: event not written: {e}"); // knife' \
   a_write_that_fails_leaves_the_caller_alone
# EV5 a month file goes before its whole month is past keep_days
ek events.rs '        if !month_end_ms(&name).is_some_and(|end| end <= cutoff) {' \
   '        if !month_end_ms(&name).is_some_and(|end| end <= cutoff + 31 * 86_400_000) { // knife' \
   old_months_go_whole_and_other_files_stay
# EV6 a seat code walks out of seat-now
ek events.rs "    if seat.is_empty() || seat.contains(['/', '\\\\', '.']) {" '    if seat.is_empty() { // knife' \
   a_seat_that_is_not_named_is_null_with_no_cards
# EV7 a seat that is not named still reads a status line
ek events.rs '        let status_item = seat.and_then(|s| seat_item(root, s));' \
   '        let status_item = seat.and_then(|s| seat_item(root, s)).or(seat_item(root, "a9136")); // knife' \
   a_seat_that_is_not_named_is_null_with_no_cards
