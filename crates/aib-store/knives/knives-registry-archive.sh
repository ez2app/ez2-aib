#!/usr/bin/env bash
# aib-knives: os=unix
# registry_archive (f163c REVIEW-regarchive-code pre d698a7ef L4): alias loops and chain length, the batch table, no double archiving, steps only while running, every archived uid; seat_next (6770f 8b-1, f163c REVIEW-merge r0 M1): a switch needs something, keeps fields a later batch leaves out, is taken once and undone per batch
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# ra-chain (f163c code review pre d698a7ef)
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  '    up + 1 + down > ALIAS_HOPS_MAX' \
  '    up + down > ALIAS_HOPS_MAX' \
  --expect-fail registry_archive::tests::mg10_a_chain_longer_than_the_hops_is_refused \
  -- cargo test -p aib-store --locked --lib registry_archive::tests::mg10_a_chain_longer_than_the_hops_is_refused
# ra-loop (f163c code review pre d698a7ef)
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  '                    return Err(StoreError::Invalid("alias loop"));' \
  '                    break;' \
  --expect-fail registry_archive::tests::mg10_aliases_resolve_and_refuse_loops_and_long_chains \
  -- cargo test -p aib-store --locked --lib registry_archive::tests::mg10_aliases_resolve_and_refuse_loops_and_long_chains
# ra-trans (f163c code review pre d698a7ef)
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  '        "done" | "failed" => &["run"],' \
  '        "done" | "failed" => &["run", "done", "undone"],' \
  --expect-fail registry_archive::tests::a_batch_moves_only_along_its_table \
  -- cargo test -p aib-store --locked --lib registry_archive::tests::a_batch_moves_only_along_its_table
# ra-double (f163c code review pre d698a7ef)
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  '            if live > 0 {' \
  '            if live > 1 {' \
  --expect-fail registry_archive::tests::archive_then_restore_then_archive_again \
  -- cargo test -p aib-store --locked --lib registry_archive::tests::archive_then_restore_then_archive_again
# ra-run (f163c code review pre d698a7ef)
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  '            if st.as_deref() != Some("run") {' \
  '            if st.as_deref() == Some("none") {' \
  --expect-fail registry_archive::tests::mg9_steps_are_pending_first_in_order_and_only_while_running \
  -- cargo test -p aib-store --locked --lib registry_archive::tests::mg9_steps_are_pending_first_in_order_and_only_while_running
# ra-uids (f163c code review pre d698a7ef)
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  '            "SELECT uid FROM archived_items WHERE kind = ?1 AND restored_at IS NULL ORDER BY uid",' \
  '            "SELECT uid FROM archived_items WHERE kind = ?1 AND restored_at IS NULL ORDER BY uid LIMIT 500",' \
  --expect-fail registry_archive::tests::every_archived_uid_is_read_past_the_list_limit \
  -- cargo test -p aib-store --locked --lib registry_archive::tests::every_archived_uid_is_read_past_the_list_limit
# ra-failed (f163c regarch N4)
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  '        "undone" => &["done", "partial_undo"],' \
  '        "undone" => &["done", "failed", "partial_undo"],' \
  --expect-fail registry_archive::tests::a_batch_moves_only_along_its_table \
  -- cargo test -p aib-store --locked --lib registry_archive::tests::a_batch_moves_only_along_its_table
# sn-something
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  'persona_next.is_some() || brain_next.is_some(),' \
  'true,' \
  --expect-fail 'registry_archive::tests::mg16_a_seat_switch_needs_something_checked_and_undo_clears_one_batch' \
  -- cargo test -p aib-store --locked --lib 'registry_archive::tests::mg16_a_seat_switch_needs_something_checked_and_undo_clears_one_batch'
# sn-keep
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  'persona_next = COALESCE(excluded.persona_next, seat_next.persona_next), \' \
  'persona_next = excluded.persona_next, \' \
  --expect-fail 'registry_archive::tests::mg16_a_seat_switch_waits_for_the_next_open_and_is_taken_once' \
  -- cargo test -p aib-store --locked --lib 'registry_archive::tests::mg16_a_seat_switch_waits_for_the_next_open_and_is_taken_once'
# sn-once
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  'if row.is_some() {' \
  'if row.is_some() && false {' \
  --expect-fail 'registry_archive::tests::mg16_a_seat_switch_waits_for_the_next_open_and_is_taken_once' \
  -- cargo test -p aib-store --locked --lib 'registry_archive::tests::mg16_a_seat_switch_waits_for_the_next_open_and_is_taken_once'
# sn-undo1
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  '"DELETE FROM seat_next WHERE batch_id = ?1"' \
  '"DELETE FROM seat_next WHERE batch_id = ?1 OR 1"' \
  --expect-fail 'registry_archive::tests::mg16_a_seat_switch_needs_something_checked_and_undo_clears_one_batch' \
  -- cargo test -p aib-store --locked --lib 'registry_archive::tests::mg16_a_seat_switch_needs_something_checked_and_undo_clears_one_batch'
# ra-undo-done (stack10 84bdef21b1: merge_undo on a done batch ended partial_undo, every step refused)
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  '        "undone" => matches!(batch_state, "run" | "done" | "partial_undo"),' \
  '        "undone" => matches!(batch_state, "run" | "done" | "partial_undo") && batch_state != "done",' \
  --expect-fail registry_archive::tests::a_done_batch_s_steps_can_be_undone_but_not_done_again \
  -- cargo test -p aib-store --locked --lib registry_archive::tests::a_done_batch_s_steps_can_be_undone_but_not_done_again
# ra-undo-failed (N4: a failed batch was already put back)
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  '        "undone" => matches!(batch_state, "run" | "done" | "partial_undo"),' \
  '        "undone" => matches!(batch_state, "run" | "done" | "partial_undo" | "failed"),' \
  --expect-fail registry_archive::tests::mg9_steps_are_pending_first_in_order_and_only_while_running \
  -- cargo test -p aib-store --locked --lib registry_archive::tests::mg9_steps_are_pending_first_in_order_and_only_while_running
# ra-done-run (R3: a done step only while the batch runs)
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  '        "done" => batch_state == "run",' \
  '        "done" => batch_state == "run" || batch_state == "done",' \
  --expect-fail registry_archive::tests::a_done_batch_s_steps_can_be_undone_but_not_done_again \
  -- cargo test -p aib-store --locked --lib registry_archive::tests::a_done_batch_s_steps_can_be_undone_but_not_done_again
# ra-step-gate (R3: the store asks step_moves_in before moving a step)
python3 -I scripts/knife.py crates/aib-store/src/registry_archive.rs \
  '                    .is_some_and(|bs| step_moves_in(&s, bs)),' \
  '                    .is_some_and(|bs| step_moves_in(&s, bs) || true),' \
  --expect-fail registry_archive::tests::mg9_steps_are_pending_first_in_order_and_only_while_running \
  -- cargo test -p aib-store --locked --lib registry_archive::tests::mg9_steps_are_pending_first_in_order_and_only_while_running
