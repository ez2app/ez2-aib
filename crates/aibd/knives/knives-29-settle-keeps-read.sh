#!/usr/bin/env bash
# a row the seat read or closed while it was being written is not settled over (b4297 st2 41fcd024b)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
# SK1 settled_item writes delivered / pending / refused over a row that is no longer queued
python3 -I scripts/knife.py crates/aibd/src/seat_send.rs \
  '    if item.get("status").and_then(Value::as_str) != Some("queued") {' \
  '    if item.get("status").and_then(Value::as_str) == Some("no-such-status") { // knife' \
  --expect-fail a_seat_send_row_is_queued_first_then_says_how_it_ended \
  -- cargo test -p aibd --locked --lib seat_send::tests::a_seat_send_row_is_queued_first_then_says_how_it_ended
