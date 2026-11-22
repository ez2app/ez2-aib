#!/usr/bin/env bash
# PT-K (DESIGN-brainnote-08de5-1123 r2 §2/§7; f163c REVIEW-permtpl r2): permission templates ask the owner for every item turned on and every base raised, refuse reserved names, parse items strictly, name what a base cannot hold, and show what this raise opens last. Each rule broken turns its test red.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# pt-turnedon
python3 -I scripts/knife.py crates/aibd/src/perm_tpl.rs \
  '.filter(|k| new.on(k) && !old.on(k))' \
  '.filter(|k| new.on(k) && old.on(k))' \
  --expect-fail 'perm_tpl::tests::every_item_turned_on_asks_the_owner' \
  -- cargo test -p aibd --locked --lib 'perm_tpl::tests::every_item_turned_on_asks_the_owner'
# pt-baseup
python3 -I scripts/knife.py crates/aibd/src/perm_tpl.rs \
  '(Some(a), Some(b)) => b > a,' \
  '(Some(a), Some(b)) => b < a,' \
  --expect-fail 'perm_tpl::tests::the_base_raises_up_the_line_and_on_the_host_axis' \
  -- cargo test -p aibd --locked --lib 'perm_tpl::tests::the_base_raises_up_the_line_and_on_the_host_axis'
# pt-addbase
python3 -I scripts/knife.py crates/aibd/src/perm_tpl.rs \
  'matches!(base, "dgrs" | "host") || DANGEROUS' \
  'matches!(base, "host") || DANGEROUS' \
  --expect-fail 'perm_tpl::tests::adding_asks_for_dangerous_items_or_bases' \
  -- cargo test -p aibd --locked --lib 'perm_tpl::tests::adding_asks_for_dangerous_items_or_bases'
# pt-parselen
python3 -I scripts/knife.py crates/aibd/src/perm_tpl.rs \
  'if o.len() != ITEMS.len() {' \
  'if false {' \
  --expect-fail 'perm_tpl::tests::items_parse_strictly_and_have_one_form' \
  -- cargo test -p aibd --locked --lib 'perm_tpl::tests::items_parse_strictly_and_have_one_form'
# pt-noenforce
python3 -I scripts/knife.py crates/aibd/src/perm_tpl.rs \
  '(on && !given) || (!on && given && !DROPPABLE_NOW.contains(k))' \
  'on && !given' \
  --expect-fail 'perm_tpl::tests::what_a_base_cannot_hold_is_named' \
  -- cargo test -p aibd --locked --lib 'perm_tpl::tests::what_a_base_cannot_hold_is_named'
# pt-reserved
python3 -I scripts/knife.py crates/aibd/src/perm_tpl.rs \
  '(!RESERVED.iter().any(|r| r.eq_ignore_ascii_case(&n))).then_some(n)' \
  'Some(n)' \
  --expect-fail 'perm_tpl::tests::reserved_names_and_the_window_line' \
  -- cargo test -p aibd --locked --lib 'perm_tpl::tests::reserved_names_and_the_window_line'
# pt-raiselast
python3 -I scripts/knife.py crates/aibd/src/perm_tpl.rs \
  ';這次多開 {}' \
  ';多開 {}' \
  --expect-fail 'perm_tpl::tests::reserved_names_and_the_window_line' \
  -- cargo test -p aibd --locked --lib 'perm_tpl::tests::reserved_names_and_the_window_line'
# ptl-asked (f163c code review pre d698a7ef)
python3 -I scripts/knife.py crates/aibd/src/perm_tpl.rs \
  '        return Some(crate::repos::asked(a));' \
  '        return None;' \
  --expect-fail a_raise_goes_in_only_with_the_owners_ticket_for_it \
  -- cargo test -p aibd --locked --test perm_tpl_owner a_raise_goes_in_only_with_the_owners_ticket_for_it
# ptl-approved (f163c code review pre d698a7ef)
python3 -I scripts/knife.py crates/aibd/src/perm_tpl.rs \
  '    if !crate::owner_ok::approved_for(&t, a) {' \
  '    if !crate::owner_ok::approved_for(&t, a) && t.is_empty() {' \
  --expect-fail a_raise_goes_in_only_with_the_owners_ticket_for_it \
  -- cargo test -p aibd --locked --test perm_tpl_owner a_raise_goes_in_only_with_the_owners_ticket_for_it
# ptl-page (f163c code review pre d698a7ef)
python3 -I scripts/knife.py crates/aibd/src/seat_input.rs \
  '            if tpl_from_user_page || page.carried {' \
  '            if tpl_from_user_page && page.carried {' \
  --expect-fail a_raise_goes_in_only_with_the_owners_ticket_for_it \
  -- cargo test -p aibd --locked --test perm_tpl_owner a_raise_goes_in_only_with_the_owners_ticket_for_it
