#!/usr/bin/env bash
# AP-K (DESIGN-assistant-6770f-1123 r1 §2 §3; ebab8 11-23 08:00 slice 9c): a name in the purpose is close, nothing under the line is used, a higher score wins before a shorter name, a bound brain must exist, the seat names only an existing persona, never dgrs / host, a long purpose is refused, and a card with one refused item hands on nothing. Each rule broken turns its test red.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# ap-contain
python3 -I scripts/knife.py crates/aibd/src/assistant_propose.rs \
  'if p.windows(n.len()).any(|w| w == n.as_slice()) {' \
  'if false {' \
  --expect-fail 'assistant_propose::tests::scores' \
  -- cargo test -p aibd --locked --lib 'assistant_propose::tests::scores'
# ap-line
python3 -I scripts/knife.py crates/aibd/src/assistant_propose.rs \
  'if s < MATCH_AT {' \
  'if s < 0.0 {' \
  --expect-fail 'assistant_propose::tests::nothing_close_drafts_new_ones_and_the_seat_waits_for_the_persona' \
  -- cargo test -p aibd --locked --lib 'assistant_propose::tests::nothing_close_drafts_new_ones_and_the_seat_waits_for_the_persona'
# ap-higher
python3 -I scripts/knife.py crates/aibd/src/assistant_propose.rs \
  's > *bs || (s == *bs && (len < *bl || (len == *bl && uid < bu.as_str())))' \
  '(len < *bl || (len == *bl && uid < bu.as_str())) && s >= 0.0 && *bs >= 0.0' \
  --expect-fail 'assistant_propose::tests::ties_go_to_the_shorter_name_then_the_smaller_uid' \
  -- cargo test -p aibd --locked --lib 'assistant_propose::tests::ties_go_to_the_shorter_name_then_the_smaller_uid'
# ap-bound
python3 -I scripts/knife.py crates/aibd/src/assistant_propose.rs \
  '.filter(|b| brains.iter().any(|r| r.uid == *b));' \
  '.filter(|_| true);' \
  --expect-fail 'assistant_propose::tests::a_bound_brain_that_is_gone_falls_back_to_the_closest' \
  -- cargo test -p aibd --locked --lib 'assistant_propose::tests::a_bound_brain_that_is_gone_falls_back_to_the_closest'
# ap-seat
python3 -I scripts/knife.py crates/aibd/src/assistant_propose.rs \
  'if let Some(p) = chosen {' \
  'if let Some(p) = chosen.filter(|_| false) {' \
  --expect-fail 'assistant_propose::tests::an_existing_persona_and_its_brain_are_used_and_the_seat_names_it' \
  -- cargo test -p aibd --locked --lib 'assistant_propose::tests::an_existing_persona_and_its_brain_are_used_and_the_seat_names_it'
# ap-mode
python3 -I scripts/knife.py crates/aibd/src/assistant_propose.rs \
  'if !matches!(mode, "safe" | "work") {' \
  'if mode.is_empty() {' \
  --expect-fail 'assistant_propose::tests::a_bad_purpose_or_mode_is_refused' \
  -- cargo test -p aibd --locked --lib 'assistant_propose::tests::a_bad_purpose_or_mode_is_refused'
# ap-len
python3 -I scripts/knife.py crates/aibd/src/assistant_propose.rs \
  'purpose.chars().count() > PURPOSE_MAX' \
  'purpose.chars().count() > PURPOSE_MAX * 2' \
  --expect-fail 'assistant_propose::tests::a_bad_purpose_or_mode_is_refused' \
  -- cargo test -p aibd --locked --lib 'assistant_propose::tests::a_bad_purpose_or_mode_is_refused'
# ap-atomic
python3 -I scripts/knife.py crates/aibd/src/assistant_propose.rs \
  'Err(e) => return Err(e.code().to_string()),' \
  'Err(_) => {}' \
  --expect-fail 'assistant_propose::tests::a_card_with_one_refused_item_hands_on_nothing' \
  -- cargo test -p aibd --locked --lib 'assistant_propose::tests::a_card_with_one_refused_item_hands_on_nothing'
# ap-wait-persona
python3 -I scripts/knife.py crates/aibd/src/assistant_propose.rs \
  'if act == "seat_open" && waits_for_new {' \
  'if act == "seat_open" && waits_for_new && false {' \
  --expect-fail 'assistant_propose::tests::nothing_close_drafts_new_ones_and_the_seat_waits_for_the_persona' \
  -- cargo test -p aibd --locked --lib 'assistant_propose::tests::nothing_close_drafts_new_ones_and_the_seat_waits_for_the_persona'
# ap-wait-brain
python3 -I scripts/knife.py crates/aibd/src/assistant_propose.rs \
  'let waits_for_new = items' \
  'let waits_for_new = false && items' \
  --expect-fail 'assistant_propose::tests::existing_persona_with_new_brain_waits_before_seat_open' \
  -- cargo test -p aibd --locked --lib 'assistant_propose::tests::existing_persona_with_new_brain_waits_before_seat_open'
# ap-closest-brain
python3 -I scripts/knife.py crates/aibd/src/assistant_propose.rs \
  'chosen_brain = Some(u.clone());' \
  'chosen_brain = None; let _ = u;' \
  --expect-fail 'assistant_propose::tests::a_bound_brain_that_is_gone_falls_back_to_the_closest' \
  -- cargo test -p aibd --locked --lib 'assistant_propose::tests::a_bound_brain_that_is_gone_falls_back_to_the_closest'
# ap-seat-brain
python3 -I scripts/knife.py crates/aibd/src/assistant_propose.rs \
  'if let Some(b) = &chosen_brain {' \
  'if let Some(b) = chosen_brain.as_ref().filter(|_| false) {' \
  --expect-fail 'assistant_propose::tests::an_existing_persona_and_its_brain_are_used_and_the_seat_names_it' \
  -- cargo test -p aibd --locked --lib 'assistant_propose::tests::an_existing_persona_and_its_brain_are_used_and_the_seat_names_it'
