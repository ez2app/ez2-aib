#!/usr/bin/env bash
# 7b (DESIGN-brainnote-08de5-1123 §7, SPEC r2 §3.7 U8): a seat never holds more than its template; changes, restarts and the log
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# pa-more
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '        .filter(|k| !items.on(k) && !DROPPABLE_NOW.contains(k))' \
  '        .filter(|k| !items.on(k) && !DROPPABLE_NOW.contains(k) && k.is_empty())' \
  --expect-fail 'perm_apply::tests::a_seat_never_holds_more_than_its_template' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::a_seat_never_holds_more_than_its_template'
# pa-drop
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    if items.on("brain") {' \
  '    if items.on("brain") || items.on("chat") {' \
  --expect-fail 'perm_apply::tests::an_item_not_held_is_not_in_the_tool_list' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::an_item_not_held_is_not_in_the_tool_list'
# pa-open
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    !(matches!(base, "safe" | "work") && *items == Items::from_keys(gives_now(base)))' \
  '    !(matches!(base, "safe" | "work" | "dgrs") && *items == Items::from_keys(gives_now(base)))' \
  --expect-fail 'perm_apply::tests::an_open_asks_unless_it_is_exactly_the_modes_own' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::an_open_asks_unless_it_is_exactly_the_modes_own'
# pa-change
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    } else if needs_owner_change(old_base, old, new_base, new) {' \
  '    } else if needs_owner_change(old_base, old, new_base, new) && old_base.is_empty() {' \
  --expect-fail 'perm_apply::tests::a_change_is_a_raise_or_a_lower' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::a_change_is_a_raise_or_a_lower'
# pa-window
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    if !missing.is_empty() {' \
  '    if !missing.is_empty() && missing.is_empty() {' \
  --expect-fail 'perm_apply::tests::the_window_lists_the_expanded_items' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::the_window_lists_the_expanded_items'
# pa-restart
python3 -I scripts/knife.py crates/aibd/src/revive.rs \
  'crate::perm_apply::drop_tools(&mut argv, &drop);' \
  'crate::perm_apply::drop_tools(&mut argv, &drop[..0]);' \
  --expect-fail 'perm_apply::tests::every_start_and_the_brain_act_read_the_template' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::every_start_and_the_brain_act_read_the_template'
# pa-brainoff (f163c bnote L1 bound to 7b: a template with the brain off keeps brain_note out)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '            .is_some_and(|i| i.on(item)),' \
  '            .is_some_and(|_| true),' \
  --expect-fail perm_apply::tests::a_template_with_the_brain_off_keeps_the_seat_from_its_brain \
  -- cargo test -p aibd --locked --lib perm_apply::tests::a_template_with_the_brain_off_keeps_the_seat_from_its_brain
# pa-unread (f163c bnote L1 bound to 7b: a template with the brain off keeps brain_note out)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '        Err(()) => false,' \
  '        Err(()) => true,' \
  --expect-fail perm_apply::tests::a_template_with_the_brain_off_keeps_the_seat_from_its_brain \
  -- cargo test -p aibd --locked --lib perm_apply::tests::a_template_with_the_brain_off_keeps_the_seat_from_its_brain
