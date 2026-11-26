#!/usr/bin/env bash
# aib-knives: os=unix
# N-workspace wave 2 knives WD1–WD4 (7b81e 11-18): the workspace library keeps one row per node, seat and
# role and stops a node at its limit. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
wd() { python3 -I scripts/knife.py crates/aib-store/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-store --locked --lib "workspace::tests::$3"; }
# WD1 the same node, seat and role a second time is not "already there"
wd '            if there > 0 {' \
   '            if there > 1 { // knife' \
   one_node_seat_and_role_is_one_row_and_a_node_takes_at_most_its_limit
# WD2 a node takes one more than its limit
wd '            if n >= ASSIGN_PER_NODE_MAX {' \
   '            if n > ASSIGN_PER_NODE_MAX { // knife' \
   one_node_seat_and_role_is_one_row_and_a_node_takes_at_most_its_limit
# WD3 a version 1 file is not brought up to 2 (its assignments table made again)
wd '            if v == 0 {' \
   '            if v <= 1 { // knife' \
   the_nodes_are_filled_once_and_a_version_1_file_keeps_its_assignments
# WD4 the node table is filled a second time
wd '            if n > 0 {' \
   '            if n > 99 { // knife' \
   the_nodes_are_filled_once_and_a_version_1_file_keeps_its_assignments
