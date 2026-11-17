#!/usr/bin/env bash
# verify_fix knives VF1–VF4 (a9136 11-18, DECISION-council-gaps-519d0-1117 R2): a failed check stays open
# until the card is passed, the same finding written twice is one, a new head is a new key, and a finding
# already told is not told again. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
vf() { python3 -I scripts/knife.py crates/aib-drive/src/act_verify.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --lib "act_verify::tests::$3"; }
# VF1 a pass does not close the failed check
vf '            _ if s(&v, "to") == PASS => {' \
   '            _ if false && s(&v, "to") == PASS => { // knife' \
   a_fail_is_open_until_the_card_is_passed
# VF2 the same finding written twice is told twice
vf '                if !list.contains(&o) {' \
   '                if true || !list.contains(&o) { // knife' \
   a_fail_is_open_until_the_card_is_passed
# VF3 a new head failed again keeps the old key (never told)
vf '    format!("{ACTION}:{}:{}:{}:{}", o.card, o.head, o.verifier, o.issue)' \
   '    format!("{ACTION}:{}:{}:{}:{}", o.card, o.head.len() * 0, o.verifier, o.issue) // knife' \
   a_fail_is_open_until_the_card_is_passed
# VF4 a finding already told is told again every round
vf '        if told {' \
   '        if false && told { // knife' \
   findings_go_to_their_fixer_once_and_one_dm_per_seat
