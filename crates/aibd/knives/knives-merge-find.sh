#!/usr/bin/env bash
# MF-K (DESIGN-merge-6770f-1123 r1 §2): the alike-name finder normalises -2 tails, scores same / contained names, keeps two customers apart, links at 0.70, merges by default only when every pair is ≥ 0.85, keeps built-ins and the plain name, and looks at no more than ITEMS_MAX. Each rule broken turns its test red.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# mf-tail
python3 -I scripts/knife.py crates/aibd/src/merge_find.rs \
  'head.strip_suffix(['\''-'\'', '\''_'\'', '\'' '\''])' \
  'head.strip_suffix(['\''_'\'', '\'' '\''])' \
  --expect-fail 'merge_find::tests::mg1_the_tail_forms_and_what_is_not_a_tail' \
  -- cargo test -p aibd --locked --lib 'merge_find::tests::mg1_the_tail_forms_and_what_is_not_a_tail'
# mf-same
python3 -I scripts/knife.py crates/aibd/src/merge_find.rs \
  '        return 1.0;' \
  '        return 0.5;' \
  --expect-fail 'merge_find::tests::mg1_a_dash_2_twin_is_the_same_name_merged_keeping_the_plain_one' \
  -- cargo test -p aibd --locked --lib 'merge_find::tests::mg1_a_dash_2_twin_is_the_same_name_merged_keeping_the_plain_one'
# mf-contain
python3 -I scripts/knife.py crates/aibd/src/merge_find.rs \
  'sl * 2 >= ll {' \
  'sl * 2 >= ll * 9 {' \
  --expect-fail 'merge_find::tests::contained_name_scores_point_nine' \
  -- cargo test -p aibd --locked --lib 'merge_find::tests::contained_name_scores_point_nine'
# mf-half
python3 -I scripts/knife.py crates/aibd/src/merge_find.rs \
  'sl * 2 >= ll {' \
  'sl * 2 >= ll / 9 {' \
  --expect-fail 'merge_find::tests::contained_name_scores_point_nine' \
  -- cargo test -p aibd --locked --lib 'merge_find::tests::contained_name_scores_point_nine'
# mf-four
python3 -I scripts/knife.py crates/aibd/src/merge_find.rs \
  'if sl >= 4 && lo.contains(sh.as_str())' \
  'if sl >= 3 && lo.contains(sh.as_str())' \
  --expect-fail 'merge_find::tests::contained_name_scores_point_nine' \
  -- cargo test -p aibd --locked --lib 'merge_find::tests::contained_name_scores_point_nine'
# mf-prefix
python3 -I scripts/knife.py crates/aibd/src/merge_find.rs \
  '(Some(x), Some(y)) => norm(x) != norm(y),' \
  '(Some(x), Some(y)) => norm(x) == norm(y),' \
  --expect-fail 'merge_find::tests::mg2_two_customers_prefixes_never_join_one_side_prefixed_still_compares' \
  -- cargo test -p aibd --locked --lib 'merge_find::tests::mg2_two_customers_prefixes_never_join_one_side_prefixed_still_compares'
# mf-link
python3 -I scripts/knife.py crates/aibd/src/merge_find.rs \
  'if v >= LINK_AT {' \
  'if v >= SURE_AT {' \
  --expect-fail 'merge_find::tests::mg3_maybe_alike_is_listed_with_nothing_ticked' \
  -- cargo test -p aibd --locked --lib 'merge_find::tests::mg3_maybe_alike_is_listed_with_nothing_ticked'
# mf-sure
python3 -I scripts/knife.py crates/aibd/src/merge_find.rs \
  'sure: low >= SURE_AT,' \
  'sure: low >= LINK_AT,' \
  --expect-fail 'merge_find::tests::a_chain_group_is_maybe_unless_every_pair_is_sure' \
  -- cargo test -p aibd --locked --lib 'merge_find::tests::a_chain_group_is_maybe_unless_every_pair_is_sure'
# mf-builtin
python3 -I scripts/knife.py crates/aibd/src/merge_find.rs \
  '.filter(|x| x.builtin)' \
  '.filter(|x| !x.builtin)' \
  --expect-fail 'merge_find::tests::mg4_a_built_in_is_kept_never_ticked_and_built_ins_alone_are_not_listed' \
  -- cargo test -p aibd --locked --lib 'merge_find::tests::mg4_a_built_in_is_kept_never_ticked_and_built_ins_alone_are_not_listed'
# mf-ticked
python3 -I scripts/knife.py crates/aibd/src/merge_find.rs \
  'if !self.sure {' \
  'if self.sure {' \
  --expect-fail 'merge_find::tests::mg1_a_dash_2_twin_is_the_same_name_merged_keeping_the_plain_one' \
  -- cargo test -p aibd --locked --lib 'merge_find::tests::mg1_a_dash_2_twin_is_the_same_name_merged_keeping_the_plain_one'
# mf-tie
python3 -I scripts/knife.py crates/aibd/src/merge_find.rs \
  '.then(has_number_tail(&a.name).cmp(&has_number_tail(&b.name)))' \
  '.then(has_number_tail(&b.name).cmp(&has_number_tail(&a.name)))' \
  --expect-fail 'merge_find::tests::the_same_input_gives_the_same_answer' \
  -- cargo test -p aibd --locked --lib 'merge_find::tests::the_same_input_gives_the_same_answer'
# mf-max
python3 -I scripts/knife.py crates/aibd/src/merge_find.rs \
  'let items = &items[..items.len().min(ITEMS_MAX)];' \
  'let items = &items[..];' \
  --expect-fail 'merge_find::tests::at_most_items_max_are_looked_at' \
  -- cargo test -p aibd --locked --lib 'merge_find::tests::at_most_items_max_are_looked_at'
