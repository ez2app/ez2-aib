#!/usr/bin/env bash
# pa2 L1–L4 (f163c REVIEW-pa2-postmerge L1; c2f0b REVIEW-pa2-postmerge L2 L3 L4): a take-back moves only aibd's own note of the row's seat; an open's window says who and which brand first; a take-back's window names the template as it is now and says when it changed or is gone. Each rule broken turns its test red.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
L1=brain_note::tests::a_take_back_moves_only_a_path_aibd_writes
# pa2-l1-dir (a path outside AIB-notes is moved)
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '        && *dir == FOLDER' \
  '        && !dir.is_empty()' \
  --expect-fail "$L1" \
  -- cargo test -p aibd --locked --lib "$L1"
# pa2-l1-seat (a note of another seat is moved)
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '    let Some(stamp) = head.strip_suffix(code).and_then(|h| h.strip_suffix('"'"'-'"'"')) else {' \
  '    let Some(stamp) = head.rsplit_once('"'"'-'"'"').map(|(h, _)| h) else {' \
  --expect-fail "$L1" \
  -- cargo test -p aibd --locked --lib "$L1"
# pa2-l1-month (a month folder that is not the stamp's)
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '        && t[4..6] == m[5..]' \
  '        && t[4..6] == t[4..6]' \
  --expect-fail "$L1" \
  -- cargo test -p aibd --locked --lib "$L1"
# pa2-l1-row (the take-back of a row moves before the path is checked)
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '    if !note_rel_ok(rel, code) {' \
  '    if false && !note_rel_ok(rel, code) {' \
  --expect-fail "$L1" \
  -- cargo test -p aibd --locked --lib "$L1"
# pa2-l2-line (the open's line without who / which brand first)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    format!("{persona_name}({persona_uid})· {provider} · {line}")' \
  '    format!("{line}{persona_name}{persona_uid}{provider}")' \
  --expect-fail 'perm_apply::tests::an_open_window_says_who_and_which_brand_first' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::an_open_window_says_who_and_which_brand_first'
# pa2-l2-open (the template open's window is left as it was)
python3 -I scripts/knife.py crates/aibd/src/seats.rs \
  '                a.resource = crate::perm_apply::open_line(' \
  '                a.by = crate::perm_apply::open_line(' \
  --expect-fail 'a_full_permission_seat_from_a_template_is_always_asked' \
  -- cargo test -p aibd --locked --test seats_page_tpl_dgrs_asks 'a_full_permission_seat_from_a_template_is_always_asked'
# pa2-l3-uid (a removed template named by its bare tpl_uid)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '        None => format!("已刪的模板 · 當時 rev {then}"),' \
  '        None => format!("{tpl_uid} · 當時 rev {then}"),' \
  --expect-fail 'perm_apply::tests::a_take_back_window_names_the_template_as_it_is_now' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::a_take_back_window_names_the_template_as_it_is_now'
# pa2-l4-changed (a template changed since is not said so)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '        Some((name, rev)) => format!("{name} · 當時 rev {then},現已改到 rev {rev}"),' \
  '        Some((name, _rev)) => name.to_owned(),' \
  --expect-fail 'perm_apply::tests::a_take_back_window_names_the_template_as_it_is_now' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::a_take_back_window_names_the_template_as_it_is_now'
# pa2-l4-mode (a seat's own mode called a removed template)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    if let Some(mode) = tpl_uid.strip_prefix("mode.") {' \
  '    if let Some(mode) = tpl_uid.strip_prefix("mode:") {' \
  --expect-fail 'perm_apply::tests::a_take_back_window_names_the_template_as_it_is_now' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::a_take_back_window_names_the_template_as_it_is_now'
