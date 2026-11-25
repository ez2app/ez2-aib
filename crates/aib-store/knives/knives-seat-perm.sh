#!/usr/bin/env bash
# 7b store: seat_perm rows and log
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# sp-action
python3 -I scripts/knife.py crates/aib-store/src/seat_perm.rs \
  '            matches!(action, "lower" | "raise_pending" | "revoke"),' \
  '            matches!(action, "lower" | "raise_pending" | "revoke" | "raise"),' \
  --expect-fail 'seat_perm::tests::a_bad_shape_is_refused' \
  -- cargo test -p aib-store --locked --lib 'seat_perm::tests::a_bad_shape_is_refused'
# sp-revoke
python3 -I scripts/knife.py crates/aib-store/src/seat_perm.rs \
  ' WHERE seat_id = ?1 AND log_id = ?2 AND revoked_at IS NULL' \
  ' WHERE seat_id = ?1 AND log_id = ?2' \
  --expect-fail 'seat_perm::tests::a_log_row_is_taken_back_once' \
  -- cargo test -p aib-store --locked --lib 'seat_perm::tests::a_log_row_is_taken_back_once'
# sp-applied
python3 -I scripts/knife.py crates/aib-store/src/seat_perm.rs \
  '                "restart_applied",' \
  '                "lower",' \
  --expect-fail 'seat_perm::tests::a_change_waits_for_the_restart_and_is_logged' \
  -- cargo test -p aib-store --locked --lib 'seat_perm::tests::a_change_waits_for_the_restart_and_is_logged'
