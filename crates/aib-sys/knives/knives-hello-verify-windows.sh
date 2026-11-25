#!/usr/bin/env bash
# aib-knives: os=windows
# aib-knives: job=rust-windows
# S10-3b-1 (PLAN-ap26-windows-first-c2f0b-1125 W-1a; 3e53b READ-winhello-wiring W3): the BCrypt
# signature check against the fixed vector. Runs in CI's Windows cell (no Hello needed). Each
# knife, put in, must turn its own test red. Run from the repo root.
set -eu
# WHV1 every signature verifies (the BCrypt answer is not read)
python3 -I scripts/knife.py crates/aib-sys/src/hello_ncrypt.rs \
  '    st == 0' \
  '    st == 0 || digest.len() == 32 // knife: always yes' \
  --expect-fail the_fixed_vector_verifies_and_any_change_does_not \
  -- cargo test -p aib-sys --locked --lib the_fixed_vector_verifies_and_any_change_does_not
