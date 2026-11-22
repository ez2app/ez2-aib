#!/usr/bin/env bash
# 指紋批次簽 B1 pure part (DESIGN-ownerbatch-impl-08de5-1123 r0, f163c REVIEW r0): groups, signed alone, order in the hash and the window, what fits, duplicates
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# ob-signed
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '    if SIGNED.contains(&op) {' \
  '    if SIGNED.is_empty() && SIGNED.contains(&op) {' \
  --expect-fail owner_batch::tests::one_window_holds_one_group_and_no_signed_act \
  -- cargo test -p aibd --locked --lib owner_batch::tests::one_window_holds_one_group_and_no_signed_act
# ob-group
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '            Ok(g) if g != group => return Err(BatchError::Mixed { i, reason: "group" }),' \
  '            Ok(g) if g != group && g == group => return Err(BatchError::Mixed { i, reason: "group" }),' \
  --expect-fail owner_batch::tests::one_window_holds_one_group_and_no_signed_act \
  -- cargo test -p aibd --locked --lib owner_batch::tests::one_window_holds_one_group_and_no_signed_act
# ob-long
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '    if let Some(i) = lines.iter().position(|l| l.chars().count() > LINE_CHARS) {' \
  '    if let Some(i) = lines.iter().position(|l| l.chars().count() > LINE_CHARS * 10) {' \
  --expect-fail owner_batch::tests::what_does_not_fit_one_window_is_refused \
  -- cargo test -p aibd --locked --lib owner_batch::tests::what_does_not_fit_one_window_is_refused
# ob-big
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '    if asks.len() > max {' \
  '    if asks.len() > max + MAX_ITEMS {' \
  --expect-fail owner_batch::tests::what_does_not_fit_one_window_is_refused \
  -- cargo test -p aibd --locked --lib owner_batch::tests::what_does_not_fit_one_window_is_refused
# ob-window
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '        if used > WINDOW_CHARS {' \
  '        if used > WINDOW_CHARS * 2 {' \
  --expect-fail owner_batch::tests::what_does_not_fit_one_window_is_refused \
  -- cargo test -p aibd --locked --lib owner_batch::tests::what_does_not_fit_one_window_is_refused
# ob-order
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '    for (i, a) in asks.iter().enumerate() {' \
  '    for (i, a) in { let mut s: Vec<&Ask> = asks.iter().collect(); s.sort_by(|x, y| x.resource.cmp(&y.resource)); s }.into_iter().enumerate() {' \
  --expect-fail owner_batch::tests::the_hash_and_the_window_keep_the_order \
  -- cargo test -p aibd --locked --lib owner_batch::tests::the_hash_and_the_window_keep_the_order
# ob-lines
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '    asks.iter()' \
  '    asks.iter().take(2)' \
  --expect-fail owner_batch::tests::the_hash_and_the_window_keep_the_order \
  -- cargo test -p aibd --locked --lib owner_batch::tests::the_hash_and_the_window_keep_the_order
# ob-idem
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '        o.remove("idem_key");' \
  '        o.remove("idem_key_");' \
  --expect-fail owner_batch::tests::the_same_act_twice_is_one_item \
  -- cargo test -p aibd --locked --lib owner_batch::tests::the_same_act_twice_is_one_item
# ob-many
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '        (Some(_), Some(_)) => Err("many_asks"),' \
  '        (Some(a), Some(_)) => Ok(a),' \
  --expect-fail owner_batch::tests::an_item_is_batched_only_with_one_ask \
  -- cargo test -p aibd --locked --lib owner_batch::tests::an_item_is_batched_only_with_one_ask
