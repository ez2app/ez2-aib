#!/usr/bin/env bash
# H7 of the "aib-sys held knives (macOS)" step, moved here word for word (519d0 11-16 23:5x: new knives
# live in crates/<crate>/knives/, aibd.yml keeps one call; 5d33a #794 0153e4f0 L1). The step calls this
# file in place of the inline knife. aib-xtask knife-anchors reads crates/*/knives/*.sh. Run from the
# repo root; -eo pipefail is what GitHub's `shell: bash` gave the step.
set -eo pipefail
set -eu
# H7 (043dd 11-15) a go byte already waiting when a late child reads it
# starts the program after its limit
python3 -I scripts/knife.py crates/aib-sys/src/held.rs \
  '            1 if go[0] == GO && monotonic_ms() >= deadline => return Err(go_too_late()),' \
  '            1 if go[0] == GO && monotonic_ms() >= deadline && go[0] != GO => return Err(go_too_late()), // knife' \
  --expect-fail a_go_byte_read_after_the_limit_does_not_start_the_program \
  -- cargo test -p aib-sys --locked --test held_late_go a_go_byte_read_after_the_limit_does_not_start_the_program
