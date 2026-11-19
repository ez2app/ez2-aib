#!/usr/bin/env bash
# N-workspace wave 2 knives NI1–NI7 (7b81e 11-18, MODEL-workspace-tree-7b81e-1118 r1, a9136 10:54): the tree file
# comes into aibd's node table once, only with AIBD_TREE_IN_DB=1; a repeated id keeps the first and the dropped
# ids are named in the event; a parent that is the node itself or not in the file makes a root; the other fields
# stay in extra; once filled, the table (not the file) is the tree for assignments too, and only while the flag is on. Each rule, broken, turns
# its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
nu() { python3 -I scripts/knife.py crates/aibd/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "workspace::tests::$3"; }
ne() { python3 -I scripts/knife.py crates/aibd/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test tree_in_db "$3"; }
# NI1 a repeated id goes without a word
nu '            dropped.push(id.to_owned());' \
   '            dropped.clear(); // knife' \
   a_tree_file_becomes_rows_once_per_id_and_reads_back_alike
# NI2 a parent that is the node itself or is not in the file is kept
nu '        let parent = str_of(n, "parent").filter(|p| *p != id && ids.contains(p));' \
   '        let parent = str_of(n, "parent").filter(|p| *p != id || ids.contains(p)); // knife' \
   a_tree_file_becomes_rows_once_per_id_and_reads_back_alike
# NI3 the fields with no column are lost
nu '                    .filter(|(k, _)| !NODE_COLUMNS.contains(&k.as_str()))' \
   '                    .filter(|(k, _)| false && !NODE_COLUMNS.contains(&k.as_str())) // knife' \
   a_tree_file_becomes_rows_once_per_id_and_reads_back_alike
# NI4 the flag is not read: the tree never comes in
ne '    std::env::var("AIBD_TREE_IN_DB").as_deref() == Ok("1")' \
   '    std::env::var("AIBD_TREE_IN_DB").as_deref() == Ok("1") && false // knife' \
   the_tree_comes_into_aibd_once_and_the_table_is_the_tree
# NI5 the event does not name the dropped ids
ne '                    "dropped_ids": dropped.iter().take(50).collect::<Vec<_>>(),' \
   '                    "dropped_ids": dropped.iter().take(0).collect::<Vec<_>>(), // knife' \
   the_tree_comes_into_aibd_once_and_the_table_is_the_tree
# NI6 assignments keep reading the file after the tree came in
ne '    tree_in_db().or_else(|| tree_path().and_then(|p| read_tree(&p).ok().flatten()))' \
   '    None.or_else(|| tree_path().and_then(|p| read_tree(&p).ok().flatten())) // knife' \
   the_tree_comes_into_aibd_once_and_the_table_is_the_tree
# NI7 the table stays the tree with the flag off (no way back)
ne '    tree_in_db_on().then_some(())?;' \
   '    Some(())?; // knife' \
   the_tree_comes_into_aibd_once_and_the_table_is_the_tree
