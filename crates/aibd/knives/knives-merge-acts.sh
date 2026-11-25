#!/usr/bin/env bash
# aibd §8 8b-3 (IFACE-merge r3): built-in page only, plan_sha verified against preview,
# a merged name loses the A13 format set and stays within NAME_MAX_CHARS.
# Every knife keeps each name it touches in use (a knife that leaves a name unused is a
# compile error, not a bite: receipt ae921-merge-acts-8b3-r2 line 66).
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
# ma-userpage
python3 -I scripts/knife.py crates/aibd/src/seat_input.rs \
  '            if merge_from_user_page || page.carried {' \
  '            if (merge_from_user_page || page.carried) && false {' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
# ma-plan
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '    if computed != plan_sha {' \
  '    if computed != plan_sha && false {' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
# ma-name
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '    if trimmed.is_empty() || trimmed.chars().count() > NAME_MAX_CHARS {' \
  '    if trimmed.is_empty() || trimmed.chars().count() > NAME_MAX_CHARS * 1000 {' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
# ma-fmt
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '    let cleaned: String = s.chars().filter(|c| !format_char(*c)).collect();' \
  '    let cleaned: String = s.chars().filter(|c| !(format_char(*c) && false)).collect();' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
