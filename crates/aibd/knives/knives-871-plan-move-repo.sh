#!/usr/bin/env bash
# #871 the handoff's repo line is absolute on this platform (b4297 11-21, 519d0 NOTE-871-win-fix-519d0-1121):
# the test fixtures are this platform's shape, and the other platform's shape is not taken. Run from the repo root.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# PM1 any non-empty repo line is taken (the other platform's absolute shape, a relative path)
python3 -I scripts/knife.py crates/aibd/src/seat_plan_move.rs \
  '        Path::new(rest).is_absolute().then(|| rest.to_owned())' \
  '        (!rest.is_empty()).then(|| rest.to_owned()) // knife' \
  --expect-fail the_repo_is_the_handoffs_repo_line \
  -- cargo test -p aibd --locked --lib seat_plan_move::tests::the_repo_is_the_handoffs_repo_line
