#!/usr/bin/env bash
# aibd §8 8b-3 (IFACE-merge r3): built-in page only, plan_sha verified against preview,
# and undo expiry enforced.
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
# ma-userpage
python3 -I scripts/knife.py crates/aibd/src/seat_input.rs \
  '            if merge_from_user_page || page.carried {' \
  '            if false && (merge_from_user_page || page.carried) {' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
# ma-plan
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '    if computed != plan_sha {' \
  '    if false && computed != plan_sha {' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
# ma-name
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '    if trimmed.is_empty() || trimmed.chars().count() > NAME_MAX_CHARS {' \
  '    if trimmed.is_empty() {' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
