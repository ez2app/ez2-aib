#!/usr/bin/env bash
# aib-knives: os=unix
# SR1 / SR2 (a9136 11-20, ORDER-autodrive 4): the parent counts a preview's running seats from
# the preview's store, read only; a waiting session counts, a session with no seat does not.
# Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
python3 -I scripts/knife.py crates/aib-store/src/sessions.rs \
  "WHERE seat_id IS NOT NULL AND (state = 'running' OR state = 'pending')\"," \
  "WHERE seat_id IS NOT NULL AND (state = 'running')\", /* knife */" \
  --expect-fail a_store_another_aibd_owns_is_counted_read_only \
  -- cargo test -p aib-store --locked --test sessions a_store_another_aibd_owns_is_counted_read_only
python3 -I scripts/knife.py crates/aib-store/src/sessions.rs \
  "WHERE seat_id IS NOT NULL AND (state = 'running' OR state = 'pending')\"," \
  "WHERE (state = 'running' OR state = 'pending')\", /* knife */" \
  --expect-fail a_store_another_aibd_owns_is_counted_read_only \
  -- cargo test -p aib-store --locked --test sessions a_store_another_aibd_owns_is_counted_read_only
