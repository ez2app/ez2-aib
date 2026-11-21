#!/usr/bin/env bash
# PS1–PS4 (7b81e 11-20; N-pv-env-rest): the tree and the board's usage file are read where the
# parent names them (AIBD_TREE_SOURCE / AIBD_USAGE_SOURCE) and handed to a preview by path, never
# copied. Each, broken, turns its own test red.
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
pu() { python3 -I scripts/knife.py crates/aibd/src/preview.rs "$1" "$2" \
  --expect-fail preview::tests::a_preview_reads_its_parents_cards_and_gets_its_brain_candidates \
  -- cargo test -p aibd --locked --lib preview::tests::a_preview_reads_its_parents_cards_and_gets_its_brain_candidates; }
us() { python3 -I scripts/knife.py crates/aibd/src/usage.rs "$1" "$2" \
  --expect-fail the_usage_file_is_read_where_aibd_usage_source_names_it \
  -- cargo test -p aibd --locked --test node_usage_source the_usage_file_is_read_where_aibd_usage_source_names_it; }
# PS1 a source that is not there is handed (the preview would read nothing)
pu '        (p.is_absolute() && p.is_file()).then_some((k, p))' \
   '        p.is_absolute().then_some((k, p)) // knife'
# PS2 the usage file is not handed
pu '        crate::usage::USAGE_SOURCE_ENV,' \
   '        // knife'
# PS3 a relative source is handed
pu '        (p.is_absolute() && p.is_file()).then_some((k, p))' \
   '        p.is_file().then_some((k, p)) // knife'
# PS4 AIBD_USAGE_SOURCE is not read
us '    let Some(path) = usage_path() else {' \
   '    let Some(path) = crate::workspace::data_file(NODE_USAGE_FILE) else { // knife'
