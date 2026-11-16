#!/usr/bin/env bash
# aibd Windows knives (RULES §21): run by the "aibd knives (Windows)" step in .github/workflows/aibd.yml,
# every crates/aibd/knives/*-windows*.sh in name order. Each knife, put in, must turn its test red.
set -eu
# J0F1 (1ced7 #806 L1): aibd closes J0 itself, the Job's kill code replaces its own exit code
python3 -I scripts/knife.py crates/aibd/src/main.rs \
  '    std::mem::forget(kill_on_exit);' \
  '    drop(kill_on_exit); // knife' \
  --expect-fail a_config_refused_after_j0_still_exits_2 \
  -- cargo test -p aibd --locked --test cli
