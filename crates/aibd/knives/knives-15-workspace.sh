#!/usr/bin/env bash
# N-workspace knives WS1–WS13 (7b81e 11-18, ORDER-workspace-519d0-1118): GET tree / host / version.
# The tree keeps the first of a repeated id, a node parented to itself is a root, a parent loop is walked
# once, depth and file order hold, the person's words go to the built-in page only, a large file is not read;
# version says which providers open and which modes a page types into; the routes are mounted and the server
# tells the module its data folder. Each rule, broken, turns its own test red. Run from the repo root.
set -eo pipefail
set -eu
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
# knife-spec reads only $1..$9 of a function (no default-value form): one wrapper per test target
wu() { python3 -I scripts/knife.py crates/aibd/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --lib "workspace::tests::$3"; }
we() { python3 -I scripts/knife.py crates/aibd/src/workspace.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test workspace "$3"; }
wg() { python3 -I scripts/knife.py crates/aibd/src/guard.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test workspace "$3"; }
wv() { python3 -I scripts/knife.py crates/aibd/src/server.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aibd --locked --test workspace "$3"; }
# WS1 a repeated id: the last one stands
wu '            ids.entry(id).or_insert(i);' \
   '            let _ = ids.insert(id, i); // knife' \
   the_tree_is_read_in_file_order_under_a_root_to_a_depth
# WS2 a node parented to itself is nobody's child and no root
wu '        match str_of(n, "parent").filter(|p| ids.contains_key(p) && *p != id) {' \
   '        match str_of(n, "parent").filter(|p| ids.contains_key(p)) { // knife' \
   the_tree_is_read_in_file_order_under_a_root_to_a_depth
# WS3 a parent loop is walked again and again
wu '        if !seen.insert(i) {' \
   '        if false && !seen.insert(i) { // knife' \
   the_tree_is_read_in_file_order_under_a_root_to_a_depth
# WS4 depth is not kept
wu '        if d < limit {' \
   '        if true || d < limit { // knife' \
   the_tree_is_read_in_file_order_under_a_root_to_a_depth
# WS5 nodes come in walk order, not file order
wu '    keep.sort_unstable();' \
   '    keep.reverse(); // knife' \
   the_tree_is_read_in_file_order_under_a_root_to_a_depth
# WS6 the station is not the tree's status
wu '        "station".into(),' \
   '        "status".into(), // knife' \
   the_tree_is_read_in_file_order_under_a_root_to_a_depth
# WS7 a user page gets the person's words
wu '        if with_need {' \
   '        if with_need || true { // knife' \
   a_user_page_gets_the_length_and_hash_of_the_persons_words
# WS8 a file over the limit is read
wu '    if meta.len() > TREE_MAX_BYTES {' \
   '    if meta.len() > TREE_MAX_BYTES * 2 { // knife' \
   a_tree_file_is_absent_too_large_or_not_a_tree
# WS9 moved to knives-26-brands.sh BR4 / BR5 (N-ws-brands: the line it cut is now brand_ready)
# WS10 a page may type into dgrs seats whatever this machine set
wu '            "input_modes": if dgrs_input { json!(["safe", "work", "dgrs"]) } else { json!(["safe", "work"]) },' \
   '            "input_modes": if dgrs_input || true { json!(["safe", "work", "dgrs"]) } else { json!(["safe", "work"]) }, // knife' \
   version_names_what_opens_and_what_a_page_types_into
# WS11 a credential a user folder page got reads the words while the built-in page serves `/`
we '        Caller::Page(p) if p.user_page => false,' \
   '        Caller::Page(p) if p.user_page && false => false, // knife' \
   the_workspace_reads_the_tree_the_host_and_what_aibd_can_do
# WS12 the tree route is not mounted
wg '    TREE,' \
   '    // knife' \
   the_workspace_reads_the_tree_the_host_and_what_aibd_can_do
# WS13 the module never learns the data folder
wv '    crate::workspace::set_data_dir(dirs.data_dir());' \
   '    // knife' \
   the_workspace_reads_the_tree_the_host_and_what_aibd_can_do
# WS14 GET personas names where the card text is (043dd add-a-seat picker; uid and name only)
we '            .map(|(uid, name)| json!({"uid": uid, "display_name": name,' \
   '            .map(|(uid, name)| json!({"uid": uid, "display_name": name, "where": "card", // knife' \
   the_workspace_reads_the_tree_the_host_and_what_aibd_can_do
