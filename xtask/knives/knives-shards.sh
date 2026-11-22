#!/usr/bin/env bash
# aib-xtask knife-shards knives (Andy 11-22: crate-knives cells by each script's seconds, not by name order).
# Run by the "aib-xtask knives (macOS)" step in aibd.yml (one line runs xtask/knives/*.sh).
# Run from the repo root.
#
set -eo pipefail
umask 022  # S88: self-hosted runners start at 077; knives must see the mode GitHub runners give (022)
ks() { python3 -I scripts/knife.py xtask/src/knife_shards.rs "$1" "$2" --expect-fail "$3" \
       -- cargo test -p aib-xtask --locked "knife_shards::tests::$3"; }
# KH1 lightest first: the two heavy scripts are placed last, wherever the light ones left room
ks '    order.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.as_bytes().cmp(b.1.as_bytes())));' \
   '    order.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.as_bytes().cmp(b.1.as_bytes())));' \
   the_heaviest_scripts_go_to_different_cells_and_every_script_gets_one_cell
# KH2 the fullest cell takes the next script: everything lands in cell 1
ks '            if *l < load[k] {' \
   '            if *l > load[k] {' \
   the_heaviest_scripts_go_to_different_cells_and_every_script_gets_one_cell
# KH3 a script the table does not know weighs next to nothing instead of the median
ks '    median(&secs.values().copied().collect::<Vec<_>>())' \
   '    median(&secs.values().copied().collect::<Vec<_>>()).min(1)' \
   a_script_the_table_does_not_know_weighs_the_tables_median_and_a_stale_line_gets_no_cell
# KH4 a path that is in the table twice is taken (the later line wins without a word)
ks '        if out.insert(path.to_string(), secs).is_some() {' \
   '        if out.insert(path.to_string(), secs).is_some() && false {' \
   a_table_line_that_is_not_seconds_tab_path_is_refused_with_its_line
# KH5 seconds that are not digits are let through to the parser's own reading
ks "        if secs.is_empty() || !secs.bytes().all(|b| b.is_ascii_digit()) {" \
   "        if secs.is_empty() {" \
   a_table_line_that_is_not_seconds_tab_path_is_refused_with_its_line
# KH6 a script listed twice gets two cells
ks '        if !seen.insert(line.to_string()) {' \
   '        if !seen.insert(line.to_string()) && false {' \
   a_list_with_a_path_twice_or_no_path_is_refused
# KH7 red runs count towards a script's seconds
ks '            let Some((file, tail)) = rest.split_once(": rc=0 secs=") else {' \
   '            let Some((file, tail)) = rest.split_once(" secs=") else {' \
   the_table_is_the_median_of_the_green_runs_only
