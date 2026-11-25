#!/usr/bin/env bash
# aib-knives: os=unix
# BN-S (DESIGN-brainnote-08de5-1123 r1 §1d): the brain_notes ledger keeps a note's path checked and takes a note back once. Each rule broken turns its test red.
set -euo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
cd "$(git rev-parse --show-toplevel)"
# bn-storepath
python3 -I scripts/knife.py crates/aib-store/src/brain_notes.rs \
  '            if s.is_empty() || s == "." || s == ".." || s.ends_with('\''.'\'') || s.ends_with('\'' '\'') {' \
  '            if false {' \
  --expect-fail 'brain_notes::tests::a_bad_row_is_refused' \
  -- cargo test -p aib-store --locked --lib 'brain_notes::tests::a_bad_row_is_refused'
# bn-storetake
python3 -I scripts/knife.py crates/aib-store/src/brain_notes.rs \
  'WHERE note_id = ?1 AND deleted_at IS NULL",' \
  'WHERE note_id = ?1",' \
  --expect-fail 'brain_notes::tests::a_note_is_kept_listed_and_taken_back_once' \
  -- cargo test -p aib-store --locked --lib 'brain_notes::tests::a_note_is_kept_listed_and_taken_back_once'
