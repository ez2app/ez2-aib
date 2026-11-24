#!/usr/bin/env bash
# aib-knives: os=unix
# N-workspace-c4 C knives RC1–RC2 (7b81e 11-18; 519d0 20:07, Andy 20:0x「這應該是 rust 程式化的」): at start the
# node table is checked against aib-plan's file id by id, and any difference keeps the file as the tree.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# RC1 a card moved in the file but not in the table is not seen
python3 -I scripts/knife.py crates/aibd/src/workspace.rs \
  '        r.station.as_deref(),' \
  '        None::<&str>, // knife' \
  --expect-fail workspace::tests::the_table_and_the_file_are_checked_id_by_id \
  -- cargo test -p aibd --locked --lib workspace::tests::the_table_and_the_file_are_checked_id_by_id
# RC2 a card only the file has is not seen
python3 -I scripts/knife.py crates/aibd/src/workspace.rs \
  '            None => only_file.push(&r.node_id),' \
  '            None => only_file.truncate(only_file.len()), // knife (only_file stays used: -D warnings)' \
  --expect-fail workspace::tests::the_table_and_the_file_are_checked_id_by_id \
  -- cargo test -p aibd --locked --lib workspace::tests::the_table_and_the_file_are_checked_id_by_id
