#!/usr/bin/env bash
# aib-knives: os=unix
# aib-plan judge knives JD1–JD4 (a9136 11-17, S107-judge: written / judged station side by side):
# each rule broken on purpose must turn its own test red. Run by the "aib-plan knives (macOS)" step in
# aibd.yml (one loop over crates/aib-plan/knives/*.sh). Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
kj() { python3 -I scripts/knife.py crates/aib-plan/src/judge.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-plan --locked --lib "$3"; }
# JD1 stations are tried left to right: the lowest passing one wins, a merged card reads 審過未推
kj 'const RIGHT: [usize; 3] = [5, 4, 3];' 'const RIGHT: [usize; 3] = [3, 4, 5]; // knife' \
   a_card_behind_its_proof_is_judged_forward_through_the_open_pr_list_and_main
# JD2 a card written ahead of its proof keeps the written station
kj '        .unwrap_or(DEV);' '        .unwrap_or(DEV).max(wi); // knife' \
   a_card_written_ahead_of_its_proof_is_judged_back_and_says_what_is_missing
# JD3 a PR missing from the open list is asked again one by one (the one-call list stops counting)
kj '                    .map_or((false, String::new()), |p| (true, p.2.clone())),' \
   '                    .map_or((true, String::new()), |p| (true, p.2.clone())), // knife' \
   the_open_pr_list_answers_for_every_number_and_falls_back_when_missing
# JD4 quiet dev cards are listed too (every open branch floods the table)
kj '        .filter(|j| !(j.agree && w.station(&j.written).map(|x| x.0) == Some(DEV)))' \
   '        .filter(|j| j.agree || !j.agree) // knife' \
   only_the_middle_stations_are_judged_and_quiet_dev_cards_are_left_out
