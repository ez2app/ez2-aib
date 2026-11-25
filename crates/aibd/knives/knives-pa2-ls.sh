#!/usr/bin/env bash
# pa2 L1–L4 (f163c REVIEW-pa2-postmerge L1; c2f0b REVIEW-pa2-postmerge L2 L3 L4): a take-back moves only aibd's own note of the row's seat; an open's window says who and which brand first (brand and uid before the name); a take-back's window names the template as it is now and says when it changed or is gone. Each rule broken turns its test red.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# pa2-l1-dir (a path outside AIB-notes is moved)
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '        && *dir == FOLDER' \
  '        && !dir.is_empty()' \
  --expect-fail 'brain_note::tests::a_take_back_moves_only_a_path_aibd_writes' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::a_take_back_moves_only_a_path_aibd_writes'
# pa2-l1-seat (a note of another seat is moved)
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '    let Some(stamp) = head.strip_suffix(code).and_then(|h| h.strip_suffix('"'"'-'"'"')) else {' \
  '    let Some(stamp) = head.rsplit_once('"'"'-'"'"').map(|(h, _)| h) else {' \
  --expect-fail 'brain_note::tests::a_take_back_moves_only_a_path_aibd_writes' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::a_take_back_moves_only_a_path_aibd_writes'
# pa2-l1-month (a month folder that is not the stamp's)
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '        && t[4..6] == m[5..]' \
  '        && t[4..6] == t[4..6]' \
  --expect-fail 'brain_note::tests::a_take_back_moves_only_a_path_aibd_writes' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::a_take_back_moves_only_a_path_aibd_writes'
# pa2-l1-row (the take-back of a row moves before the path is checked)
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '    if !note_rel_ok(rel, code) {' \
  '    if false && !note_rel_ok(rel, code) {' \
  --expect-fail 'brain_note::tests::a_take_back_moves_only_a_path_aibd_writes' \
  -- cargo test -p aibd --locked --lib 'brain_note::tests::a_take_back_moves_only_a_path_aibd_writes'
# pa2-l2-line (L-a: the name back before the brand and the uid, where a long or copied name pushes them out)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    format!("{who} · {line}")' \
  '    format!("{persona_name} · {who} · {line}")' \
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
  '        Some((name, rev)) => format!("(已改到 rev {rev},收回到 rev {then}){}", cut_name(name)),' \
  '        Some((name, _rev)) => name.to_owned(),' \
  --expect-fail 'perm_apply::tests::a_take_back_window_names_the_template_as_it_is_now' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::a_take_back_window_names_the_template_as_it_is_now'
# pa2-l4-mode (a seat's own mode called a removed template)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    if let Some(mode) = tpl_uid.strip_prefix("mode.") {' \
  '    if let Some(mode) = tpl_uid.strip_prefix("mode:") {' \
  --expect-fail 'perm_apply::tests::a_take_back_window_names_the_template_as_it_is_now' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::a_take_back_window_names_the_template_as_it_is_now'
# pa2-lb-order (L-b: the change warning back after a name the window cuts)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '        Some((name, rev)) => format!("(已改到 rev {rev},收回到 rev {then}){}", cut_name(name)),' \
  '        Some((name, rev)) => format!("{}(已改到 rev {rev},收回到 rev {then})", cut_name(name)),' \
  --expect-fail 'perm_apply::tests::a_take_back_window_names_the_template_as_it_is_now' \
  -- cargo test -p aibd --locked --lib 'perm_apply::tests::a_take_back_window_names_the_template_as_it_is_now'
# pa2-lc-delete (L-c: the page's 收回 of a note moves a file its row's path does not let it)
python3 -I scripts/knife.py crates/aibd/src/brain_note.rs \
  '            library().and_then(|root| take_back_row(&root, &rel, &code, &sha, &keep))' \
  '            library().and_then(|root| take_back_row(&root, &rel, &code, &sha, &keep).or_else(|_| take_back(&root, &rel, &sha, &keep)))' \
  --expect-fail 'a_take_back_moves_only_aibds_own_note_of_the_rows_seat' \
  -- cargo test -p aibd --locked --test brain_note_take_back_row 'a_take_back_moves_only_aibds_own_note_of_the_rows_seat'
# pa2-lc-revoke (L-c: the take-back's window does not get the template's name as it is now)
python3 -I scripts/knife.py crates/aibd/src/perm_apply.rs \
  '    e.name = match tokio::task::spawn_blocking(move || db.perm_template(&uid)).await {' \
  '    e.tpl_uid = match tokio::task::spawn_blocking(move || db.perm_template(&uid)).await {' \
  --expect-fail 'a_take_back_to_a_removed_template_says_so_in_the_window' \
  -- cargo test -p aibd --locked --test perm_revoke_window 'a_take_back_to_a_removed_template_says_so_in_the_window'
# ow-cut (L-a, ebab8 11-25 13:37: the shared window helper does not cut the AI-writable name)
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  '    let mut n: String = word.chars().take(shown).collect();' \
  '    let mut n: String = word.chars().take(usize::MAX).collect();' \
  --expect-fail 'owner_ok::tests::fixed_words_come_first_and_a_name_is_cut' \
  -- cargo test -p aibd --locked --lib 'owner_ok::tests::fixed_words_come_first_and_a_name_is_cut'
# ow-order (L-a: the shared window helper puts the name before aibd's fixed words)
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  '    format!("{fixed} · {n}")' \
  '    format!("{n} · {fixed}")' \
  --expect-fail 'owner_ok::tests::fixed_words_come_first_and_a_name_is_cut' \
  -- cargo test -p aibd --locked --lib 'owner_ok::tests::fixed_words_come_first_and_a_name_is_cut'
# a11-plain-order (L-a on main's A11: a plain dgrs open's window with the persona's name first again)
python3 -I scripts/knife.py crates/aibd/src/repos.rs \
  '            &[provider, persona_uid, "完全權限,不帶倉"],' \
  '            &[persona_name, provider, persona_uid, "完全權限,不帶倉"],' \
  --expect-fail 'repos::tests::a_dgrs_open_and_restart_say_what_they_are_and_do_not_pass_for_each_other' \
  -- cargo test -p aibd --locked --lib 'repos::tests::a_dgrs_open_and_restart_say_what_they_are_and_do_not_pass_for_each_other'
