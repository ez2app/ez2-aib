#!/usr/bin/env bash
# 網頁版待審框第 1 期 aibd 信線 (DESIGN-web-inbox-aibd-7ebca-1121 r1; DECISION-council-webinbox §4 §5)
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
wk() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" -- cargo test -p aibd --locked $5; }
# WK1 a web seat a bridged letter was put in is not woken
wk crates/aibd/src/dm_import.rs \
   '                    woken(&seat_id);' \
   '                    let _ = &seat_id; // knife' \
   a_letter_to_a_moved_code_lands_once_in_the_web_seats_inbox \
   '--lib dm_import::tests::a_letter_to_a_moved_code_lands_once_in_the_web_seats_inbox'
