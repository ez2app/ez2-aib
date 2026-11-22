#!/usr/bin/env bash
# S6-K1–K7 (DESIGN-s6-handofflatest-08de5-1123 r1; f163c REVIEW-s6 r0/r1): handoff_latest picks a code's own names before the cap, lists plain non-secret files only, takes a five-hex code and a two-field body, and answers both kinds. Each rule broken turns its test red.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# s6-prefix
python3 -I scripts/knife.py crates/aibd/src/handoff_latest.rs \
  'if !of_code(&n, code) || crate::docs::secret_name(&n) {' \
  'if crate::docs::secret_name(&n) || code.is_empty() {' \
  --expect-fail 'handoff_latest::tests::a_big_folder_still_gives_the_newest' \
  -- cargo test -p aibd --locked --lib 'handoff_latest::tests::a_big_folder_still_gives_the_newest'
# s6-cap
python3 -I scripts/knife.py crates/aibd/src/handoff_latest.rs \
  'if out.len() == NAMES_MAX {' \
  'if false {' \
  --expect-fail 'handoff_latest::tests::a_big_folder_still_gives_the_newest' \
  -- cargo test -p aibd --locked --lib 'handoff_latest::tests::a_big_folder_still_gives_the_newest'
# s6-plain
python3 -I scripts/knife.py crates/aibd/src/handoff_latest.rs \
  'if !m.file_type().is_file() {' \
  'if false {' \
  --expect-fail 'handoff_latest::tests::a_link_is_not_listed' \
  -- cargo test -p aibd --locked --lib 'handoff_latest::tests::a_link_is_not_listed'
# s6-secret
python3 -I scripts/knife.py crates/aibd/src/handoff_latest.rs \
  'if !of_code(&n, code) || crate::docs::secret_name(&n) {' \
  'if !of_code(&n, code) {' \
  --expect-fail 'handoff_latest::tests::a_link_is_not_listed' \
  -- cargo test -p aibd --locked --lib 'handoff_latest::tests::a_link_is_not_listed'
# s6-code
python3 -I scripts/knife.py crates/aibd/src/handoff_latest.rs \
  '.filter(|c| crate::seat_plan_move::code_ok(c))' \
  '.filter(|c| !c.is_empty())' \
  --expect-fail 'handoff_latest::tests::only_a_five_hex_code_is_taken' \
  -- cargo test -p aibd --locked --lib 'handoff_latest::tests::only_a_five_hex_code_is_taken'
# s6-fields
python3 -I scripts/knife.py crates/aibd/src/handoff_latest.rs \
  '    fields_ok.then_some(code)' \
  '    (fields_ok || !fields_ok).then_some(code)' \
  --expect-fail 'handoff_latest::tests::only_a_five_hex_code_is_taken' \
  -- cargo test -p aibd --locked --lib 'handoff_latest::tests::only_a_five_hex_code_is_taken'
# s6-mempack
python3 -I scripts/knife.py crates/aibd/src/handoff_latest.rs \
  '"mempack": one(crate::seat_carry::MEMPACK),' \
  '"mempack": one(crate::seat_carry::HANDOFF),' \
  --expect-fail 'handoff_latest::tests::the_newest_of_the_seats_own_is_given' \
  -- cargo test -p aibd --locked --lib 'handoff_latest::tests::the_newest_of_the_seats_own_is_given'
