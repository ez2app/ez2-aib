#!/usr/bin/env bash
# aib-plan migrate MG1–MG6 (a9136 11-18, DECISION-council-gaps-519d0-1117 R1 / R5 + 5d33a / 0eed7 spot checks):
# a paused card takes the reason it was paused for, a paused head held back by the queue is not in flight,
# a card already in the seven stations is not migrated, a parent card is left to its children, an owner
# with no 審 / 驗 / 疊 segment is not split, and a 驗 segment goes to the verifier. Each rule, broken, turns
# its own test red. Run on macOS (the header default) by the shared crate-knives step. Run from the repo
# root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
mg() { python3 -I scripts/knife.py "crates/aib-plan/src/$1" "$2" "$3" --expect-fail "$4" \
         -- cargo test -p aib-plan --locked --lib "$4"; }
# MG1 the reason is the last segment, not the one that says → 暫停 (5d33a S79-switch)
mg migrate.rs '            s.contains(&format!("→ {st}"))' \
   '            true // knife' \
   a_paused_card_takes_the_reason_it_was_paused_for
# MG2 a paused card with a head held back by the in-flight limit counts as in flight (5d33a S76-close)
mg migrate.rs '                            let pos = if st == "暫停" && head.is_some() && !queued {' \
   '                            let pos = if st == "暫停" && head.is_some() && (queued || !queued) { // knife' \
   a_paused_card_takes_the_reason_it_was_paused_for
# MG3 a card already in the seven stations is moved by migrate
mg card.rs '                if w.names.iter().any(|x| x == cur) || cur == w.void {' \
   '                if false && (w.names.iter().any(|x| x == cur) || cur == w.void) { // knife' \
   migrate_writes_only_cards_off_the_seven_stations
# MG4 a parent card is migrated instead of left to its children (0eed7 S89)
mg migrate.rs '            if parents.contains(n.id.as_str()) {' \
   '            if false && parents.contains(n.id.as_str()) { // knife' \
   old_station_names_map_to_the_seven_stations_the_council_agreed
# MG5 an owner with no 審 / 驗 / 疊 segment is split anyway
mg card.rs '                if !marked {' \
   '                if false && !marked { // knife' \
   migrate_writes_only_cards_off_the_seven_stations
# MG6 a 驗 segment is read as an author
mg migrate.rs "        let bucket = if seg.contains('驗') {" \
   "        let bucket = if false && seg.contains('驗') { // knife" \
   a_mixed_owner_is_split_into_author_reviewer_and_verifier
