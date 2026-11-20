#!/usr/bin/env bash
# a seat reads and closes a post that named it in a shared conversation (0eed7 VERIFY-pv6 M2)
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
# SR1 a t_ thread is refused like a pair thread it is not a party of
python3 -I scripts/knife.py crates/aibd/src/messages.rs \
  '    let shared = thread_id.starts_with("t_");' \
  '    let shared = thread_id.is_empty(); // knife' \
  --expect-fail a_seat_closes_a_post_that_named_it_in_a_shared_conversation \
  -- cargo test -p aibd --locked --test seat_send a_seat_closes_a_post_that_named_it_in_a_shared_conversation
# SR2 another member reads a row that was not sent to it
python3 -I scripts/knife.py crates/aibd/src/messages.rs \
  '            .filter(|r| r.thread_id == t && (!shared || r.to_key == m))' \
  '            .filter(|r| r.thread_id == t && (!shared || !r.to_key.is_empty())) // knife' \
  --expect-fail a_seat_closes_a_post_that_named_it_in_a_shared_conversation \
  -- cargo test -p aibd --locked --test seat_send a_seat_closes_a_post_that_named_it_in_a_shared_conversation
# SR3 the timeline's read mark moves to a delivery row
python3 -I scripts/knife.py crates/aibd/src/messages.rs \
  '    let keep_mark = !shared;' \
  '    let keep_mark = !thread_id.is_empty(); // knife' \
  --expect-fail a_seat_closes_a_post_that_named_it_in_a_shared_conversation \
  -- cargo test -p aibd --locked --test seat_send a_seat_closes_a_post_that_named_it_in_a_shared_conversation
