#!/usr/bin/env bash
# 指紋批次簽 B1 (DESIGN-ownerbatch-impl-08de5-1123 r0, f163c REVIEW r0 + list r0 + wire pre): groups, signed alone, order, fit, duplicates; collecting, one window, the list taken once, child tickets bound to the collected ask, done-without-window told as done; the pairing read again before each item
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
# ob-delim
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '            a.resource.len(),' \
  '            0,' \
  --expect-fail owner_batch::tests::the_hash_and_the_window_keep_the_order \
  -- cargo test -p aibd --locked --lib owner_batch::tests::the_hash_and_the_window_keep_the_order
# obw-collect
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  '        return Asked::Ticket(format!("{COLLECTED}{n}"));' \
  '        let _ = n;' \
  --expect-fail owner_ok::tests::a_collected_ask_raises_nothing_and_stays_with_its_call \
  -- cargo test -p aibd --locked --lib owner_ok::tests::a_collected_ask_raises_nothing_and_stays_with_its_call
# obw-shape
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  'pub const COLLECTED: &str = "collected.";' \
  'pub const COLLECTED: &str = "collected_";' \
  --expect-fail owner_ok::tests::a_collected_ask_raises_nothing_and_stays_with_its_call \
  -- cargo test -p aibd --locked --lib owner_ok::tests::a_collected_ask_raises_nothing_and_stays_with_its_call
# obw-used
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  '        if v.rec.used {' \
  '        if v.rec.used && !v.rec.used {' \
  --expect-fail owner_ok::tests::one_window_for_a_list_then_one_ticket_an_item \
  -- cargo test -p aibd --locked --lib owner_ok::tests::one_window_for_a_list_then_one_ticket_an_item
# obw-taken
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  '        let ok = b.rec.operation == OP_BATCH && b.rec.approved && b.rec.used;' \
  '        let ok = b.rec.operation == OP_BATCH && b.rec.approved;' \
  --expect-fail owner_ok::tests::one_window_for_a_list_then_one_ticket_an_item \
  -- cargo test -p aibd --locked --lib owner_ok::tests::one_window_for_a_list_then_one_ticket_an_item
# obw-bind
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  '                    params_sha256: params_sha256.clone(),' \
  '                    params_sha256: String::new(),' \
  --expect-fail owner_ok::tests::one_window_for_a_list_then_one_ticket_an_item \
  -- cargo test -p aibd --locked --lib owner_ok::tests::one_window_for_a_list_then_one_ticket_an_item
# obw-lines
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  '            b.lines.join("\n")' \
  '            b.lines.first().cloned().unwrap_or_default()' \
  --expect-fail owner_ok::tests::one_window_for_a_list_then_one_ticket_an_item \
  -- cargo test -p aibd --locked --lib owner_ok::tests::one_window_for_a_list_then_one_ticket_an_item
# obw-sign
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  '        let msg = crate::owner_batch::binding(&b.ticket, &b.batch_sha, &b.by);' \
  '        let msg = crate::owner_batch::binding(&b.ticket, "", &b.by);' \
  --expect-fail owner_ok::tests::one_window_for_a_list_then_one_ticket_an_item \
  -- cargo test -p aibd --locked --lib owner_ok::tests::one_window_for_a_list_then_one_ticket_an_item
# obw-told
python3 -I scripts/knife.py crates/aibd/src/owner_ok.rs \
  '        if let Some(f) = told {' \
  '        if let Some(f) = told.filter(|_| false) {' \
  --expect-fail owner_ok::tests::one_window_for_a_list_then_one_ticket_an_item \
  -- cargo test -p aibd --locked --lib owner_ok::tests::one_window_for_a_list_then_one_ticket_an_item
# obw-work
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '            body["repo"].is_string() && body["mode"].as_str().is_some_and(|m| m != "work")' \
  '            body["repo"].is_string() || body["mode"].as_str().is_some_and(|m| m != "work")' \
  --expect-fail owner_batch::tests::only_acts_that_ask_first_are_collected \
  -- cargo test -p aibd --locked --lib owner_batch::tests::only_acts_that_ask_first_are_collected
# obw-move
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '    broke && group == Group::Move' \
  '    broke && group == Group::Push' \
  --expect-fail owner_batch::tests::a_failed_move_step_stops_the_rest_only_in_its_group \
  -- cargo test -p aibd --locked --lib owner_batch::tests::a_failed_move_step_stops_the_rest_only_in_its_group
# obw-merged
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '        let mut v = b.states[*k].clone();' \
  '        let mut v = ended("waiting", None, None);' \
  --expect-fail owner_batch::tests::a_merged_item_reads_as_the_kept_one \
  -- cargo test -p aibd --locked --lib owner_batch::tests::a_merged_item_reads_as_the_kept_one
# obw-done
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '    if status.is_success() {' \
  '    if status.is_success() && false {' \
  --expect-fail owner_batch::tests::an_item_done_without_a_window_is_told_as_done \
  -- cargo test -p aibd --locked --lib owner_batch::tests::an_item_done_without_a_window_is_told_as_done
# obi-gone
python3 -I scripts/knife.py crates/aibd/src/owner_batch.rs \
  '    let Some(page) = crate::owner_ok::page_now(digest).filter(|p| p.confirmed) else {' \
  '    let Some(page) = Some(crate::pairing::PageAccess { digest, confirmed: true, user_page: false, carried: false }) else {' \
  --expect-fail one_yes_does_the_list_and_collecting_it_writes_nothing \
  -- cargo test -p aibd --locked --test owner_batch_signs one_yes_does_the_list_and_collecting_it_writes_nothing
