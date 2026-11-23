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
  '            .is_some_and(|i| i.on(item))' \
  '            .is_some_and(|_| true)' \
  --expect-fail perm_apply::tests::a_template_with_the_brain_off_keeps_the_seat_from_its_brain \
  -- cargo test -p aibd --locked --lib perm_apply::tests::a_template_with_the_brain_off_keeps_the_seat_from_its_brain
# pa-unread (f163c bnote L1 bound to 7b: a template with the brain off keeps brain_note out)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '        Err(()) => false,' \
  '        Err(()) => true,' \
  --expect-fail perm_apply::tests::a_template_with_the_brain_off_keeps_the_seat_from_its_brain \
  -- cargo test -p aibd --locked --lib perm_apply::tests::a_template_with_the_brain_off_keeps_the_seat_from_its_brain
# tpl-ask (ebab8 11-23 09:00: a full-permission seat from a template is always asked once)
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '            if crate::perm_apply::open_needs_owner(&e.base, &e.items) {' \
  '            if false && crate::perm_apply::open_needs_owner(&e.base, &e.items) {' \
  --expect-fail a_full_permission_seat_from_a_template_is_always_asked \
  -- cargo test -p aibd --locked --test seats_page_tpl_dgrs_asks a_full_permission_seat_from_a_template_is_always_asked
# tpl-base (ebab8 11-23 09:00: a full-permission seat from a template is always asked once)
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '            if e.base != req.mode {' \
  '            if e.base == "never" {' \
  --expect-fail a_full_permission_seat_from_a_template_is_always_asked \
  -- cargo test -p aibd --locked --test seats_page_tpl_dgrs_asks a_full_permission_seat_from_a_template_is_always_asked
# tpl-one (ebab8 11-23 09:00: a full-permission seat from a template is always asked once)
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '        && req.tpl_uid.is_none()' \
  '        && req.mode == "dgrs"' \
  --expect-fail a_full_permission_seat_from_a_template_is_always_asked \
  -- cargo test -p aibd --locked --test seats_page_tpl_dgrs_asks a_full_permission_seat_from_a_template_is_always_asked
# pa-m1 (f163c REVIEW-papply M1)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    if !written {' \
  '    if false && !written {' \
  --expect-fail perm_apply::tests::a_restart_that_could_not_take_its_change_holds_less \
  -- cargo test -p aibd --locked --lib perm_apply::tests::a_restart_that_could_not_take_its_change_holds_less
# pa-l5 (f163c REVIEW-papply L5)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    if next != Change::Same && change_of(mode, now, mode, new) == Change::Raise {' \
  '    if false && next != Change::Same && change_of(mode, now, mode, new) == Change::Raise {' \
  --expect-fail perm_apply::tests::a_smaller_template_under_a_waiting_raise_is_still_asked \
  -- cargo test -p aibd --locked --lib perm_apply::tests::a_smaller_template_under_a_waiting_raise_is_still_asked
# pa-applyok (f163c REVIEW-papply L2)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '        if !crate::owner_ok::approved_for(&t, &a) {' \
  '        if t.is_empty() {' \
  --expect-fail a_raise_on_an_open_seat_goes_in_only_with_its_own_ticket \
  -- cargo test -p aibd --locked --test perm_apply_owner a_raise_on_an_open_seat_goes_in_only_with_its_own_ticket
# pa-rev (f163c REVIEW-papply L2)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    if t.rev != rev {' \
  '    if t.rev != rev && false {' \
  --expect-fail a_raise_on_an_open_seat_goes_in_only_with_its_own_ticket \
  -- cargo test -p aibd --locked --test perm_apply_owner a_raise_on_an_open_seat_goes_in_only_with_its_own_ticket
# pa-userpage (f163c REVIEW-papply L2)
python3 -I scripts/knife.py crates/aibd/src/seat_input.rs \
  '            if apply_from_user_page || page.carried {' \
  '            if false && apply_from_user_page || page.carried {' \
  --expect-fail a_raise_on_an_open_seat_goes_in_only_with_its_own_ticket \
  -- cargo test -p aibd --locked --test perm_apply_owner a_raise_on_an_open_seat_goes_in_only_with_its_own_ticket
# tpl-ticket (f163c REVIEW-papply L2 page_open)
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '                if !crate::owner_ok::approved_for(t, &a) {' \
  '                if t.is_empty() {' \
  --expect-fail a_full_permission_seat_from_a_template_is_always_asked \
  -- cargo test -p aibd --locked --test seats_page_tpl_dgrs_asks a_full_permission_seat_from_a_template_is_always_asked
# tpl-pair (f163c REVIEW-papply L2 page_open)
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '        _ => return Refusal("template", "tpl_uid and rev go together").into_response(),' \
  '        _ => None,' \
  --expect-fail a_full_permission_seat_from_a_template_is_always_asked \
  -- cargo test -p aibd --locked --test seats_page_tpl_dgrs_asks a_full_permission_seat_from_a_template_is_always_asked
# rs-take (f163c REVIEW-papply M2)
python3 -I scripts/knife.py crates/aibd/src/revive.rs \
  '    let drop = crate::perm_apply::at_restart(st, &seat.seat_id).await;' \
  '    let drop: Vec<&'"'"'static str> = Vec::new();' \
  --expect-fail a_waiting_change_takes_effect_on_every_restart_of_every_brand \
  -- cargo test -p aibd --locked --test perm_restart_paths a_waiting_change_takes_effect_on_every_restart_of_every_brand
# pa-l6 (f163c REVIEW-papply L6)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '                Some(w) => on(&w),' \
  '                Some(_) => true,' \
  --expect-fail perm_apply::tests::a_template_with_the_brain_off_keeps_the_seat_from_its_brain \
  -- cargo test -p aibd --locked --lib perm_apply::tests::a_template_with_the_brain_off_keeps_the_seat_from_its_brain
