#!/usr/bin/env bash
# N-web-dmbridge knives LS1-LS6 (7ebca 11-19; 519d0 02:08, 5d33a CHECK-webdm-legacy, 0eed7
# VERIFY-dmbridge): a letter into the 1.x ledger only from a page paired this run and with the
# switch in bridge.json; a header-shaped line is refused; the cabin and the seat folder are
# checked; an old-style seat's answer comes back only for a person's letter. Each rule, broken,
# turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
ll() { python3 -I scripts/knife.py crates/aibd/src/legacy_send.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
lt() { python3 -I scripts/knife.py crates/aibd/src/legacy_send.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test legacy_send "$3"; }
dl() { python3 -I scripts/knife.py crates/aibd/src/dm_import.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "$3"; }
# LS1
lt 'if page.carried || !page.confirmed {' \
   'if !page.confirmed && page.carried && false {' \
   only_a_page_paired_this_run_with_the_switch_writes_a_letter
# LS2
lt 'let Some(cabin) = cfg.legacy_cabin.clone() else {' \
   'let Some(cabin) = cfg.legacy_cabin.clone().or(Some(String::new())) else {' \
   only_a_page_paired_this_run_with_the_switch_writes_a_letter
# LS3
ll 'if line.contains(" >> ")' \
   'if line.contains(" >>> ")' \
   legacy_send::tests::r30_and_a_header_shaped_line_refuse_the_whole_letter
# LS4
ll "if cabin.is_empty() || cabin.contains(['/', '\\\\']) || cabin.contains(\"..\") {" \
   'if cabin.is_empty() {' \
   legacy_send::tests::a_seat_is_one_of_the_cabins_live_seats
# LS5
ll 'if !queue.join(seat).is_dir() {' \
   'if !queue.is_dir() {' \
   legacy_send::tests::a_letter_is_queued_in_the_ledger_and_shows_in_the_conversation
# LS6
dl 'if side_of(letter) != "hub"' \
   'if side_of(letter) == "nobody"' \
   dm_import::tests::a_seats_answer_to_a_persons_letter_comes_back_in_its_thread
