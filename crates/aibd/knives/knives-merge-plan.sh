#!/usr/bin/env bash
# MP-K (DESIGN-merge-6770f-1123 r1 §3 §5 §6; f163c REVIEW-merge r0 L1 L2): the plan sha is over sorted keys, a rollback step restores only what it changed, skips what never happened or was undone, persona and brain merges ask the owner, and the owner window is never cut. Each rule broken turns its test red.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# mp-sort
python3 -I scripts/knife.py crates/aibd/src/merge_plan.rs \
  'keys.sort_by(|a, b| a.as_bytes().cmp(b.as_bytes()));' \
  'keys.reverse();' \
  --expect-fail 'merge_plan::tests::canonical_escapes_keys_and_strings' \
  -- cargo test -p aibd --locked --lib 'merge_plan::tests::canonical_escapes_keys_and_strings'
# mp-restore
python3 -I scripts/knife.py crates/aibd/src/merge_plan.rs \
  'if now == after && now != before {' \
  'if now == after {' \
  --expect-fail 'merge_plan::tests::a_step_that_changes_nothing_is_skipped' \
  -- cargo test -p aibd --locked --lib 'merge_plan::tests::a_step_that_changes_nothing_is_skipped'
# mp-skip
python3 -I scripts/knife.py crates/aibd/src/merge_plan.rs \
  'if now == before {' \
  'if now == before && false {' \
  --expect-fail 'merge_plan::tests::mg9_a_pending_step_rolls_back_by_what_the_value_is_now' \
  -- cargo test -p aibd --locked --lib 'merge_plan::tests::mg9_a_pending_step_rolls_back_by_what_the_value_is_now'
# mp-undone
python3 -I scripts/knife.py crates/aibd/src/merge_plan.rs \
  'if state == StepState::Undone {' \
  'if state == StepState::Undone && false {' \
  --expect-fail 'merge_plan::tests::mg12_a_done_step_changed_since_is_left_alone' \
  -- cargo test -p aibd --locked --lib 'merge_plan::tests::mg12_a_done_step_changed_since_is_left_alone'
# mp-owner
python3 -I scripts/knife.py crates/aibd/src/merge_plan.rs \
  'matches!(kind, "persona" | "brain")' \
  'matches!(kind, "persona")' \
  --expect-fail 'merge_plan::tests::only_personas_and_brains_ask_the_owner' \
  -- cargo test -p aibd --locked --lib 'merge_plan::tests::only_personas_and_brains_ask_the_owner'
# mp-sha12
python3 -I scripts/knife.py crates/aibd/src/merge_plan.rs \
  'if sha12.len() != 12 || !sha12.bytes().all(|b| b.is_ascii_hexdigit()) {' \
  'if false {' \
  --expect-fail 'merge_plan::tests::the_window_has_counts_and_the_sha12_and_is_never_cut' \
  -- cargo test -p aibd --locked --lib 'merge_plan::tests::the_window_has_counts_and_the_sha12_and_is_never_cut'
# mp-nocut
python3 -I scripts/knife.py crates/aibd/src/merge_plan.rs \
  '(t.chars().count() <= WINDOW_MAX).then_some(t)' \
  'Some(t)' \
  --expect-fail 'merge_plan::tests::the_window_has_counts_and_the_sha12_and_is_never_cut' \
  -- cargo test -p aibd --locked --lib 'merge_plan::tests::the_window_has_counts_and_the_sha12_and_is_never_cut'
