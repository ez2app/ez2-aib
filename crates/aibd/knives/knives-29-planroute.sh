#!/usr/bin/env bash
# aib-knives: os=unix
# N-workspace-c4 A knives PR1–PR4 (7b81e 11-18; DESIGN-tree-plan-via-aibd-7b81e-1118.md, 5d33a CHECK 0 H/M):
# aib-plan writes through aibd while aibd holds the tree; the route takes aib-plan's fields only,
# a write aibd did not make is seen (L2), and aib-plan calls loopback only, naming the seat only
# with the start-up token (L1).
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# PR1 a field aib-plan does not have (assign) is taken
python3 -I scripts/knife.py crates/aibd/src/plan_route.rs \
  '    if let Some(k) = o.keys().find(|k| !allowed.contains(&k.as_str())) {' \
  '    if let Some(k) = o.keys().find(|k| !allowed.contains(&k.as_str()) && false) { // knife (allowed stays used)' \
  --expect-fail plan_route::tests::an_op_takes_only_aib_plans_own_fields \
  -- cargo test -p aibd --locked --lib plan_route::tests::an_op_takes_only_aib_plans_own_fields
# PR2 a changed file with a new mtime passes as aibd's own
python3 -I scripts/knife.py crates/aibd/src/plan_route.rs \
  '    let same = sha_of(path) == Some(k.2);' \
  '    let same = true; // knife' \
  --expect-fail plan_route::tests::a_write_aibd_did_not_make_is_seen \
  -- cargo test -p aibd --locked --lib plan_route::tests::a_write_aibd_did_not_make_is_seen
# PR3 aib-plan calls a run info that is not loopback
python3 -I scripts/knife.py crates/aib-plan/src/via_aibd.rs \
  '    if !ip.is_loopback() {' \
  '    if ip.is_unspecified() { // knife' \
  --expect-fail via_aibd::tests::a_marker_with_no_aibd_writes_nothing \
  -- cargo test -p aib-plan --locked --lib via_aibd::tests::a_marker_with_no_aibd_writes_nothing
# PR4 the self-reported seat rides along with a seat's own token
python3 -I scripts/knife.py crates/aib-plan/src/via_aibd.rs \
  '        if let Some(s) = seat {' \
  '        if let Some(s) = seat.or(Some("00000")) { // knife' \
  --expect-fail via_aibd::tests::the_body_carries_the_op_and_the_seat_only_with_the_start_up_token \
  -- cargo test -p aib-plan --locked --lib via_aibd::tests::the_body_carries_the_op_and_the_seat_only_with_the_start_up_token
