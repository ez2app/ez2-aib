#!/usr/bin/env bash
# RK1 (a9136 11-18, 0eed7 VERIFY 51ac92d9 M1): the next start frees a held stop's slot but keeps
# who ended it. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# RK1 the restart overwrites who ended a held stop
python3 -I scripts/knife.py crates/aib-store/src/sessions.rs \
  'ended_by = COALESCE(ended_by, ?4),' \
  'ended_by = ?4, /* knife */' \
  --expect-fail a_held_stop_keeps_who_ended_it_when_the_next_start_frees_it \
  -- cargo test -p aib-store --locked --test sessions a_held_stop_keeps_who_ended_it_when_the_next_start_frees_it
