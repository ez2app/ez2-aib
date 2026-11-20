#!/usr/bin/env bash
# The receipt gate's reading of SSH_ORIGINAL_COMMAND (DESIGN-receipt-gate-a9136-1120 slice 1)
set -u
rg() { python3 -I scripts/knife.py crates/aib-receipt-gate/src/lib.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-receipt-gate --locked --lib "tests::$3"; }
# RG1 an interactive login (no command) is served
rg '    let w = words(command.ok_or("no_command")?)?;' '    let w = words(command.unwrap_or("status web-a9136-012345678"))?; // knife' \
   only_four_verbs_of_plain_words_are_read
# RG2 a shell character in a word is taken
rg '        .all(|b| b == b'"'"' '"'"' || (b.is_ascii_graphic() && !b"\"'"'"'`\\$;&|<>()*?[]{}~#!".contains(&b)))' \
   '        .all(|b| b == b'"'"' '"'"' || b.is_ascii_graphic() || b == b'"'"'\t'"'"' || !b.is_ascii()) // knife' \
   only_four_verbs_of_plain_words_are_read
# RG3 a doubled or edge space is taken
rg '    if w.iter().any(|x| x.is_empty()) {' '    if w.is_empty() { // knife' \
   only_four_verbs_of_plain_words_are_read
# RG4 a long line is read
rg '    if line.len() > MAX_LINE {' '    if line.len() > MAX_LINE * 100 { // knife' \
   only_four_verbs_of_plain_words_are_read
# RG5 the run id may name another head
rg '            if head9_of(id) != head.get(..9) {' '            if id.is_empty() { // knife' \
   a_run_names_its_head_and_its_sizes
# RG6 a bundle over the limit is taken
rg '    s.parse::<u64>().ok().filter(|n| *n <= max)' '    s.parse::<u64>().ok() // knife' \
   a_run_names_its_head_and_its_sizes
# RG7 a run id of another shape is taken
rg '    if web != Some("web") || !seat_ok || !again_ok || end.is_some() {' '    if end.is_some() { // knife' \
   only_four_verbs_of_plain_words_are_read
# RG8 knives with the whole workspace are taken
rg '            if *krate == "ws" && knives_len > 0 {' '            if *krate == "ws" && knives_len > MAX_KNIVES { // knife' \
   a_run_names_its_head_and_its_sizes
# RG9 a zero-length bundle is taken
rg '                .filter(|n| *n > 0)' '                .filter(|_| true) // knife' \
   a_run_names_its_head_and_its_sizes
# slice 2 (recv.rs): stdin files, knife table, base repository
rv() { python3 -I scripts/knife.py crates/aib-receipt-gate/src/recv.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-receipt-gate --locked --lib "recv::tests::$3"; }
# RV1 a file whose digest differs is kept
rv '        if hex != sha256 {' '        if hex.is_empty() { // knife' \
   a_file_is_kept_only_whole_and_with_its_digest
# RV2 a refused file is left on disk
rv '        let _ = std::fs::remove_file(out);' '        let _ = out; // knife' \
   a_file_is_kept_only_whole_and_with_its_digest
# RV3 an existing file is written over
rv '            .create_new(true)' '            .create(true).truncate(true) // knife' \
   a_file_is_kept_only_whole_and_with_its_digest
# RV4 a short stream is taken as whole
rv '            if n == 0 {' '            if n == usize::MAX { // knife' \
   a_file_is_kept_only_whole_and_with_its_digest
# RV5 a knife of another crate is taken
rv '        if *c != krate {' '        if c.is_empty() { // knife' \
   a_knife_table_is_six_fields_of_one_crate_inside_the_tree
# RV6 shell words in the test arguments are taken
rv '        if !plain(name, b"-_") || !plain(targs, b"-_: ") {' '        if !plain(name, b"-_") { // knife' \
   a_knife_table_is_six_fields_of_one_crate_inside_the_tree
# RV7 a knife file outside the tree is taken
rv '                .any(|p| p.is_empty() || p == "." || p == "..");' '                .any(|p| p.is_empty()); // knife' \
   a_knife_table_is_six_fields_of_one_crate_inside_the_tree
# RV8 a base with a hook or alternates is used
rv '    if !hooks_empty || git_dir.join("objects/info/alternates").exists() {' '    if !hooks_empty { // knife' \
   a_base_repo_the_last_run_touched_is_rebuilt
# RV9 an unknown config section is taken
rv '                _ => return false,' '                _ => "core", // knife' \
   a_base_repo_the_last_run_touched_is_rebuilt
# RV10 any core key is taken (hooksPath, fsmonitor)
rv '            "core" => CORE.contains(&k.as_str()) && plain(v, b""),' '            "core" => true, // knife' \
   a_base_repo_the_last_run_touched_is_rebuilt
# RV11 the remote may point anywhere
rv '                (k == "url" && v == BASE_URL)' '                (k == "url")' \
   a_base_repo_the_last_run_touched_is_rebuilt
