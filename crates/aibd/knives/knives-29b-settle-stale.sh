#!/usr/bin/env bash
# a settle that meets a newer revision of a still-queued row reads it again and writes, not drops (08de5 11-26, ebab8 q_6f1076bb)
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# SK2 (settle_row writes once only: a stale revision drops the write, the row says queued for good)
python3 -I scripts/knife.py crates/aibd/src/seat_send.rs \
  '    for _ in 0..SETTLE_TRIES {' \
  '    for _ in 0..SETTLE_TRIES.min(1) {' \
  --expect-fail 'seat_send::tests::a_settle_that_meets_a_newer_revision_reads_again' \
  -- cargo test -p aibd --locked --lib 'seat_send::tests::a_settle_that_meets_a_newer_revision_reads_again'
