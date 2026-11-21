#!/usr/bin/env bash
# D3 閒著的網頁席收到信要被叫醒 (DESIGN-drive-d3-wake-7ebca-1121; DEV-drive-web-519d0-1121 D3)
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
dw() { python3 -I scripts/knife.py "$1" "$2" "$3" --expect-fail "$4" -- cargo test -p aibd --locked $5; }
# DW1 nothing not yet due is ever looked at again (an idle seat is never woken)
dw crates/aibd/src/mail_nudge.rs \
   '        .filter(|at| *at > now)' \
   '        .filter(|at| *at > now && false) // knife' \
   an_idle_seats_message_is_looked_at_again_when_it_comes_due \
   '--lib mail_nudge::tests::an_idle_seats_message_is_looked_at_again_when_it_comes_due'
# DW2 an urgent message waits the three minutes like any other
dw crates/aibd/src/mail_nudge.rs \
   '    if crate::urgent::is_urgent(r) && nudged(r) == 0 {' \
   '    if crate::urgent::is_urgent(r) && nudged(r) == -1 { // knife' \
   an_urgent_message_is_named_at_once_and_a_bridged_one_waits \
   '--lib mail_nudge::tests::an_urgent_message_is_named_at_once_and_a_bridged_one_waits'
# DW3 an urgent message, once named, is named again at once (no rest)
dw crates/aibd/src/mail_nudge.rs \
   '    if crate::urgent::is_urgent(r) && nudged(r) == 0 {' \
   '    if crate::urgent::is_urgent(r) { // knife' \
   an_urgent_message_is_named_at_once_and_a_bridged_one_waits \
   '--lib mail_nudge::tests::an_urgent_message_is_named_at_once_and_a_bridged_one_waits'
# DW4 a seat gets a second look-again for the same time
dw crates/aibd/src/mail_nudge.rs \
   '    if p.get(seat).is_some_and(|t| *t <= at) {' \
   '    if p.get(seat).is_some_and(|t| *t < at) { // knife' \
   a_seat_has_one_look_again_waiting \
   '--lib mail_nudge::tests::a_seat_has_one_look_again_waiting'
# DW5 a replaced look-again still fires
dw crates/aibd/src/mail_nudge.rs \
   '    if p.get(seat) == Some(&at) {' \
   '    if p.contains_key(seat) { // knife' \
   a_seat_has_one_look_again_waiting \
   '--lib mail_nudge::tests::a_seat_has_one_look_again_waiting'
