#!/usr/bin/env bash
# aib-knives: os=unix
# The "aib-drive round knives (macOS)" step of aibd.yml's rust job, moved here word for word (11-16 裁定:
# 新刀放 crates/<套件>/knives/;IFACE-drive-shadow-519d0-1117 §5; #804's shape). aibd.yml runs every
# crates/aib-drive/knives/*.sh in name order in one step; aib-xtask knife-anchors reads them.
# Run from the repo root; -eo pipefail is what GitHub's `shell: bash` gave the step when it was inline.
#
# S79-own o5 round (b433b): one auto-push round's first step and its ledger line; each knife takes one check away.
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
set -eu
rk() { python3 -I scripts/knife.py crates/aib-drive/src/round.rs "$1" "$2" --expect-fail "$3" \
         -- cargo test -p aib-drive --locked --test round "$3"; }
# RD1 step 0 is not the first item
rk '    match o.items.first() {' '    match o.items.last() { // knife' \
   step_zero_is_the_first_item_and_the_ledger_line_names_it
# RD2 an unreadable plan asks for a none line
rk '        None if o.sources.get("plan").map(String::as_str) != Some("ok") => {' '        None if false => { // knife' \
   an_empty_list_gives_none_and_an_unreadable_plan_is_not_idleness
# RD3 the throttle is printed for every seat
rk '    if sends {' '    if sends || true { // knife' \
   the_throttle_is_printed_only_for_a_sender_with_acts_numbers
# RD4 (o5) the round line step is not printed
rk '        "{n}. 記這一輪(什麼都沒做也記):aib-drive round done --seat {seat} --recv <收幾封> --sent <發幾封> \' '        "{n}. (knife) --seat {seat} \' \
   the_throttle_is_printed_only_for_a_sender_with_acts_numbers
