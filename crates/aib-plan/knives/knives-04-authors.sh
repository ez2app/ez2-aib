#!/usr/bin/env bash
# aib-knives: os=unix
# aib-plan authors PA1–PA3 (a9136 11-17, DECISION-council-gaps-519d0-1117 R5 + 0eed7 N1 on e070fecf):
# a 審 / 驗 / 疊 segment of the owner is not the author, a seat's role is read from its own segment, and an
# author brought in by --branch in the same command cannot pass its own card. Each rule, broken, turns
# its own test red. Run on macOS (the header default) by the shared crate-knives step. Run from the repo
# root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
pa() { python3 -I scripts/knife.py crates/aib-plan/src/card.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-plan --locked --lib "$3"; }
# PA1 the first seat of the owner is the author even when it is marked 驗
pa '            .filter(|seg| !["審", "驗", "疊"].iter().any(|w| seg.contains(w)))' \
   '            .filter(|seg| !seg.is_empty()) // knife' \
   a_marked_owner_segment_is_not_the_author
# PA2 a seat's role is not read from its own segment
pa '        .find(|seg| seat_words(seg).any(|t| t == seat))' \
   '        .find(|seg| seat_words(seg).any(|t| t == seat) && false) // knife' \
   a_marked_owner_segment_is_not_the_author
# PA3 an author brought in by --branch in the same command passes its own card (0eed7 N1)
pa '                            None if authors(&probe).0.contains(seat) => {' \
   '                            None if authors(n).0.contains(seat) => { // knife' \
   a_marked_owner_segment_is_not_the_author
