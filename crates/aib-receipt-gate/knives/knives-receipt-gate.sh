#!/usr/bin/env bash
# The receipt gate's reading of SSH_ORIGINAL_COMMAND (DESIGN-receipt-gate-a9136-1120 slice 1)
set -u
rg() { python3 -I scripts/knife.py crates/aib-receipt-gate/src/lib.rs "$1" "$2" --expect-fail "$3" \
        -- cargo test -p aib-receipt-gate --locked --lib "tests::$3"; }
# RG1 an interactive login (no command) is served
rg '    let w = words(command.ok_or("no_command")?)?;' '    let w = words(command.unwrap_or("status a9136-g1-012345678"))?; // knife' \
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
rg '            if !id.ends_with(&head[..9]) {' '            if id.is_empty() { // knife' \
   a_run_names_its_head_and_its_sizes
# RG6 a bundle over the limit is taken
rg '    s.parse::<u64>().ok().filter(|n| *n <= max)' '    s.parse::<u64>().ok() // knife' \
   a_run_names_its_head_and_its_sizes
# RG7 a run id of another shape is taken
rg '        (Some(seat), Some("g1"), Some(h9), None) if hex(seat, 5) && hex(h9, 9))' '        (Some(_), Some(_), Some(h9), None) if hex(h9, 9)) // knife' \
   only_four_verbs_of_plain_words_are_read
# RG8 knives with the whole workspace are taken
rg '            if *krate == "ws" && knives_len > 0 {' '            if *krate == "ws" && knives_len > MAX_KNIVES { // knife' \
   a_run_names_its_head_and_its_sizes
# RG9 a zero-length bundle is taken
rg '                .filter(|n| *n > 0)' '                .filter(|_| true) // knife' \
   a_run_names_its_head_and_its_sizes
