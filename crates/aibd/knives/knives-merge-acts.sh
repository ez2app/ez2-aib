#!/usr/bin/env bash
# aibd §8 8b-3 (IFACE-merge r3): built-in page only, plan_sha verified against preview,
# a merged name loses the A13 format set and stays within NAME_MAX_CHARS; f163c REVIEW-macts
# M1 (window words), M2 (assignments as steps), L1 (stale plan), L2 (put back midway),
# L3 (the switch a seat had), N1 (a plan runs once), the journal numbered from 0, and on the
# undo side a put-back that fails is kept, not marked undone (da8e773f L1, N1).
# Every knife keeps each name it touches in use (a knife that leaves a name unused is a
# compile error, not a bite: receipt ae921-merge-acts-8b3-r2 line 66).
set -euo pipefail
umask 022
cd "$(git rev-parse --show-toplevel)"
# ma-userpage
python3 -I scripts/knife.py crates/aibd/src/seat_input.rs \
  '            if merge_from_user_page || page.carried {' \
  '            if (merge_from_user_page || page.carried) && false {' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
# ma-plan
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '    if computed != plan_sha {' \
  '    if computed != plan_sha && false {' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
# ma-name
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '    if trimmed.is_empty() || trimmed.chars().count() > NAME_MAX_CHARS {' \
  '    if trimmed.is_empty() || trimmed.chars().count() > NAME_MAX_CHARS * 1000 {' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
# ma-fmt
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '    let cleaned: String = s.chars().filter(|c| !format_char(*c)).collect();' \
  '    let cleaned: String = s.chars().filter(|c| !(format_char(*c) && false)).collect();' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
# ma-seq
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '        let s = self.seq;' \
  '        let s = self.seq + 1;' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
# ma-win-run
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '            resource: words, // M1 run' \
  '            resource: format!("{plan_sha}{}", &words[..0]), // M1 run' \
  --expect-fail mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run \
  -- cargo test -p aibd --locked --test merge_notes_owner mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run
# ma-win-undo
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '            resource: words, // M1 undo' \
  '            resource: format!("{}{}", batch.batch_id, &words[..0]), // M1 undo' \
  --expect-fail mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run \
  -- cargo test -p aibd --locked --test merge_notes_owner mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run
# ma-assign
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '        .filter(|r| merge_set.contains(r.role.as_str()))' \
  '        .filter(|r| merge_set.contains(r.role.as_str()) && false)' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
# ma-assign-back
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '        role: old.to_string(),' \
  '        role: keep.to_string(),' \
  --expect-fail merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo \
  -- cargo test -p aibd --locked --test merge_acts_owner merge_acts_enforce_page_only_owner_gate_plan_sha_and_undo
# ma-stale
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '    if stale {' \
  '    if stale && false {' \
  --expect-fail mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run \
  -- cargo test -p aibd --locked --test merge_notes_owner mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run
# ma-rollback
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '            let back = undo_steps(st, kind, &batch_id);' \
  '            let back = Ok::<Vec<Value>, ()>(Vec::new()).or_else(|()| undo_steps(st, kind, &batch_id));' \
  --expect-fail mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run \
  -- cargo test -p aibd --locked --test merge_notes_owner mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run
# ma-before
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '                            let b = &before["row"];' \
  '                            let b = &before["none"];' \
  --expect-fail mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run \
  -- cargo test -p aibd --locked --test merge_notes_owner mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run
# ma-once
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '                c.remove(plan_sha);' \
  '                let _ = c.get(plan_sha);' \
  --expect-fail mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run \
  -- cargo test -p aibd --locked --test merge_notes_owner mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run
# ma-settle
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '    if did && marked() {' \
  '    if did && marked() || true {' \
  --expect-fail 'merge_acts::tests::a_put_back_counts_only_when_the_store_took_it_and_the_journal_says_undone' \
  -- cargo test -p aibd --locked --lib 'merge_acts::tests::a_put_back_counts_only_when_the_store_took_it_and_the_journal_says_undone'
# ma-n1
python3 -I scripts/knife.py crates/aibd/src/merge_acts.rs \
  '            let (status, message) = if left != 0 {' \
  '            let (status, message) = if left != 0 || true {' \
  --expect-fail mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run \
  -- cargo test -p aibd --locked --test merge_notes_owner mg13_mg14_brain_notes_copy_whole_undo_takes_back_and_a_placeholder_refuses_the_run
